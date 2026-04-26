from fastapi_mail import FastMail, MessageSchema
from app.config import settings
from fastapi_mail import ConnectionConfig

async def send_email(email: str, subject: str, body: str):
    message = MessageSchema(
        subject=subject,
        recipients=[email],
        body=body,
        subtype="html"
    )

    fm = FastMail(conf)
    await fm.send_message(message)