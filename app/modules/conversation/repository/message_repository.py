from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.conversation.entity.message import MessageEntity


class MessageRepository:


    def __init__(self, db: Session):
        self.db = db


    def create(
        self,
        message: MessageEntity
    ) -> MessageEntity:

        self.db.add(message)

        self.db.commit()
        #TODO:后面升级self.db.flush()然后service统一commit

        self.db.refresh(message)

        return message


    def get_by_id(
        self,
        message_id: UUID
    ) -> MessageEntity | None:


        stmt = select(MessageEntity).where(
            MessageEntity.id == message_id
        )


        return self.db.scalar(stmt)



    def list_by_conversation(
    self,
    conversation_id: UUID,
) -> list[MessageEntity]:

        stmt = (
            select(MessageEntity)
            .where(
                MessageEntity.conversation_id == conversation_id
            )
            .order_by(
                MessageEntity.created_at
            )
        )
        messages = self.db.scalars(stmt).all()

        return messages