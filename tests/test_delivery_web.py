from datetime import datetime

from sqlalchemy.orm import Session
from test_auth import auth_app as auth_app
from test_auth import login

from app.birthdays.service import create_birthday
from app.db.models import Delivery, DeliveryAttempt


def test_journal_admin_only_and_filters(auth_app):
    app, client = auth_app
    assert client.get("/admin/deliveries", follow_redirects=False).status_code == 303
    login(client, login="пётр")
    assert client.get("/admin/deliveries").status_code == 403
    client.cookies.clear()
    login(client)
    with Session(app.state.db_engine) as db, db.begin():
        person = create_birthday(db, name="Ёжик 🎂", day=1, month=1)
        delivery = Delivery(
            birthday_id=person.id,
            user_id=1,
            occurrence_date=datetime.now().date(),
            days_before=0,
            status="failed",
            attempts=1,
            next_attempt_at=datetime.now(),
            last_error="Результат неизвестен",
        )
        db.add(delivery)
        db.flush()
        db.add(
            DeliveryAttempt(
                delivery_id=delivery.id,
                attempt_number=1,
                outcome="failed",
                error="Результат неизвестен",
            )
        )
    page = client.get("/admin/deliveries?status=failed")
    assert page.status_code == 200
    assert "Ёжик 🎂" in page.text and "История попыток" in page.text
    assert "Результат неизвестен" in page.text
    assert "Отправка в Telegram недоступна" in page.text
    assert "Ёжик 🎂" not in client.get("/admin/deliveries?status=sent").text
    assert client.get("/admin/deliveries?status=invalid").status_code == 400
