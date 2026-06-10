import datetime

from peewee import *

from src.model.BaseModel import BaseModel
from src.model.User import User


class LegendaryPirate(BaseModel):
    """
    Legendary Pirate class
    """

    user = ForeignKeyField(
        User, backref="legendary_pirates", on_delete="CASCADE", on_update="CASCADE"
    )
    epithet = CharField(max_length=99, null=True)
    reason = CharField(max_length=999, null=True)
    end_date = DateTimeField(null=True)
    original_end_date = DateTimeField(null=True)
    revoke_reason = CharField(max_length=999, null=True)
    is_permanent = BooleanField(default=False)

    class Meta:
        db_table = "legendary_pirate"

    @staticmethod
    def get_active() -> list["LegendaryPirate"]:
        """
        Get active legendary pirates
        :return: Active legendary pirates
        """

        now = datetime.datetime.now()
        return LegendaryPirate.select().where(
            (LegendaryPirate.end_date.is_null()) | (LegendaryPirate.end_date > now)
        )

    def is_active(self) -> bool:
        """
        Returns True if the legendary pirate is active
        :return: True if the legendary pirate is active
        """

        return self.end_date is None or self.end_date > datetime.datetime.now()


LegendaryPirate.create_table(safe=True)
