"""Company-reviewed equipment profile versions and explicit order bindings."""

from alembic import op

from backend.equipment.profile_models import (
    ManufacturerProfileRevocation,
    ManufacturerProfileVersion,
    OrderManufacturerProfile,
)

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    for model in (
        ManufacturerProfileVersion,
        ManufacturerProfileRevocation,
        OrderManufacturerProfile,
    ):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (
        OrderManufacturerProfile,
        ManufacturerProfileRevocation,
        ManufacturerProfileVersion,
    ):
        model.__table__.drop(op.get_bind(), checkfirst=True)
