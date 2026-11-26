"""The bronze table deployment notebook (C2): names, DDL and the schema check, with a fake Spark."""

from types import SimpleNamespace

import pytest

import deploy_bronze_table as deploy
from notebooks.common.config_loader import EnvironmentConfig

CONFIG = EnvironmentConfig(environment="uat", settings={"storage_path": "s3a://bucket-audit-uat/", "catalog_name": "lakehouse_uat", "database_name": "audit_lhuat"})
CONTRACT = [("query_id", "string"), ("event_time", "timestamp"), ("payload", "string"), ("copied_at", "timestamp"), ("copy_task", "string")]


class FakeSpark:
    """Just enough of a SparkSession for ensure_bronze_table."""

    def __init__(self, columns=None):
        self.columns = columns
        self.statements = []
        self.catalog = SimpleNamespace(tableExists=lambda name: self.columns is not None)

    def table(self, name):
        fields = [SimpleNamespace(name=n, dataType=SimpleNamespace(simpleString=lambda k=k: k)) for n, k in self.columns]
        return SimpleNamespace(schema=SimpleNamespace(fields=fields))

    def sql(self, statement):
        self.statements.append(statement)


def test_names_and_location():
    assert deploy.qualified_table(CONFIG) == "lakehouse_uat.audit_lhuat.starrocks_audit_event_fe_brz"
    assert deploy.table_location(CONFIG) == "s3a://bucket-audit-uat/lakehouse_uat/audit_lhuat/starrocks_audit_event_fe_brz"


def test_create_table_sql_is_the_contract():
    sql = deploy.create_table_sql(CONFIG)
    assert sql.startswith("CREATE TABLE lakehouse_uat.audit_lhuat.starrocks_audit_event_fe_brz (`query_id` STRING, `event_time` TIMESTAMP, `payload` STRING, ")
    assert "PARTITIONED BY (days(event_time))" in sql
    assert "LOCATION 's3a://bucket-audit-uat/lakehouse_uat/audit_lhuat/starrocks_audit_event_fe_brz'" in sql
    assert "'format-version'='2'" in sql and "'write.format.default'='parquet'" in sql


def test_missing_table_is_created():
    spark = FakeSpark()
    assert deploy.ensure_bronze_table(spark, CONFIG) == "created"
    assert spark.statements == [deploy.create_table_sql(CONFIG)]


def test_matching_table_is_left_alone():
    spark = FakeSpark([(name.upper(), kind) for name, kind in CONTRACT])
    assert deploy.ensure_bronze_table(spark, CONFIG) == "exists"
    assert spark.statements == []


@pytest.mark.parametrize(
    "columns, message",
    [
        (CONTRACT[:-1], "missing column copy_task string"),
        (CONTRACT + [("extra", "int")], "unexpected column extra int"),
        ([("query_id", "string"), ("event_time", "string"), *CONTRACT[2:]], "missing column event_time timestamp"),
        (list(reversed(CONTRACT)), "different order"),
    ],
)
def test_a_different_table_stops_the_run_without_altering_it(columns, message):
    spark = FakeSpark(columns)
    with pytest.raises(ValueError, match=message):
        deploy.ensure_bronze_table(spark, CONFIG)
    assert spark.statements == []


def test_deploy_logs_the_record():
    record = deploy.deploy(FakeSpark(), CONFIG)
    assert record == {
        "layer": "bronze",
        "environment": "uat",
        "table": "lakehouse_uat.audit_lhuat.starrocks_audit_event_fe_brz",
        "location": "s3a://bucket-audit-uat/lakehouse_uat/audit_lhuat/starrocks_audit_event_fe_brz",
        "result": "created",
    }
