from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import get_settings

# Import all *public schema* models so Alembic can detect them.
# Tenant-domain (per-tenant) tables are created via TENANT_TABLES in tenant_domain.py
# when a tenant is registered and are NOT managed by Alembic migrations.
from app.models.base import Base

# Import VerificationToken model so Alembic can detect it (public schema)

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Add your model's MetaData object here for 'autogenerate' support.
target_metadata = Base.metadata

# Load application settings (DATABASE_URL from .env)
settings = get_settings()


def get_url() -> str:
    """
    Return the database URL from our Settings.

    This makes Alembic use the same DATABASE_URL as the app,
    instead of relying on sqlalchemy.url in alembic.ini.
    """
    return settings.database_url


def include_object(object, name, type_, reflected, compare_to):
    """
    Filter which objects Alembic should include in autogenerate.

    We only want to manage PUBLIC schema tables with Alembic.
    All per-tenant tables (patients, appointments, departments, stock_items, tenant roles, etc.)
    are created dynamically per-tenant and should NOT appear in Alembic migrations.
    """
    if type_ == "table":
        schema = getattr(object, "schema", None)
        # Only include tables that explicitly live in "public" schema
        return schema == "public"

    if type_ == "index":
        # Only include indexes whose parent table is in public schema
        table = getattr(object, "table", None)
        if table is not None:
            schema = getattr(table, "schema", None)
            return schema == "public"
        return False

    # For other object types (constraints, columns), defer to Alembic defaults
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() here emit the given string to the script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we create an Engine and associate a connection with the context.
    """
    connectable = create_engine(
        get_url(),
        future=True,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # detect column type changes
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
