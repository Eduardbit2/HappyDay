from alembic import context

from app.db.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "Используйте python -m app.db upgrade/check: нужны backup и управляемая транзакция"
    )

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,
    compare_type=True,
    compare_server_default=True,
    transactional_ddl=True,
)
with context.begin_transaction():
    context.run_migrations()
