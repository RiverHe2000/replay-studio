"""Portable relational schema. All state transitions are owned by Store transactions."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()


def table(name, *columns):
    return Table(name, metadata, *columns)


schema_version = table("schema_version", Column("id", Integer, primary_key=True), Column("version", Integer, nullable=False))
auth_limits = table("auth_limits", Column("id", String(64), primary_key=True), Column("started", Float, nullable=False), Column("count", Integer, nullable=False))
users = table("users", Column("id", String(32), primary_key=True), Column("email", String(254), unique=True, nullable=False), Column("name", String(80), nullable=False), Column("password_hash", Text, nullable=False), Column("created_at", String(40), nullable=False))
sessions = table("sessions", Column("token_hash", String(64), primary_key=True), Column("user_id", ForeignKey("users.id"), nullable=False), Column("csrf", String(64), nullable=False), Column("expires", Float, nullable=False))
projects = table("projects", Column("id", String(32), primary_key=True), Column("name", String(120), nullable=False), Column("owner_id", ForeignKey("users.id"), nullable=False), Column("version", Integer, nullable=False, default=0), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False))
members = table("members", Column("project_id", ForeignKey("projects.id"), primary_key=True), Column("user_id", ForeignKey("users.id"), primary_key=True), Column("role", String(10), nullable=False))
assets = table("assets", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("name", String(240), nullable=False), Column("size", BigInteger, nullable=False), Column("sha256", String(64), nullable=False), Column("source_key", String(300), nullable=False), Column("duration", Float), Column("status", String(20), nullable=False), Column("run_id", String(32)), Column("deleted", Boolean, nullable=False, default=False), Column("created_at", String(40), nullable=False))
uploads = table("uploads", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("user_id", ForeignKey("users.id"), nullable=False), Column("filename", String(240), nullable=False), Column("size", BigInteger, nullable=False), Column("offset", BigInteger, nullable=False), Column("status", String(20), nullable=False), Column("asset_id", String(32)), Column("created_at", String(40), nullable=False))
runs = table("runs", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("asset_id", ForeignKey("assets.id"), nullable=False), Column("fingerprint", String(64), nullable=False), Column("config", JSON, nullable=False), Column("status", String(20), nullable=False), Column("manifest", JSON), Column("created_at", String(40), nullable=False))
jobs = table("jobs", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("asset_id", ForeignKey("assets.id"), nullable=False), Column("run_id", ForeignKey("runs.id")), Column("kind", String(20), nullable=False), Column("queue", String(10), nullable=False), Column("payload", JSON, nullable=False), Column("dependencies", JSON, nullable=False), Column("status", String(20), nullable=False), Column("attempts", Integer, nullable=False, default=0), Column("lease_token", String(32)), Column("lease_until", Float), Column("worker_id", String(120)), Column("artifact_prefix", String(300)), Column("result", JSON), Column("error", Text), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False))
Index("jobs_dispatch", jobs.c.status, jobs.c.queue, jobs.c.created_at)
resources = table("resources", Column("id", String(20), primary_key=True), Column("token", String(32)), Column("expires", Float, nullable=False, default=0))
workers = table("workers", Column("id", String(120), primary_key=True), Column("queue", String(10), nullable=False), Column("seen", Float, nullable=False))
timelines = table("timelines", Column("project_id", ForeignKey("projects.id"), primary_key=True), Column("version", Integer, primary_key=True), Column("asset_id", ForeignKey("assets.id"), nullable=False), Column("run_id", ForeignKey("runs.id"), nullable=False), Column("clips", JSON, nullable=False), Column("user_id", ForeignKey("users.id"), nullable=False), Column("updated_at", String(40), nullable=False))
exports = table("exports", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("user_id", ForeignKey("users.id"), nullable=False), Column("version", Integer, nullable=False), Column("asset_id", ForeignKey("assets.id"), nullable=False), Column("run_id", ForeignKey("runs.id"), nullable=False), Column("clips", JSON, nullable=False), Column("status", String(20), nullable=False), Column("job_id", ForeignKey("jobs.id"), nullable=False), Column("result", JSON), Column("created_at", String(40), nullable=False))
comments = table("comments", Column("id", String(32), primary_key=True), Column("project_id", ForeignKey("projects.id"), nullable=False), Column("user_id", ForeignKey("users.id"), nullable=False), Column("asset_id", ForeignKey("assets.id")), Column("text", Text, nullable=False), Column("time", Float), Column("created_at", String(40), nullable=False))
