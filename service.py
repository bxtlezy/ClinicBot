import hashlib
import hmac
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from database import Appointment, Database, LoginSession, Review, Specialization, User

SPECIALIZATIONS = ["Терапевт", "Хирург", "Окулист", "ЛОР", "Кардиолог", "Невролог", "Дерматолог"]
SLOTS = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
SYMPTOMS = {
    "Терапевт": ["температур", "жар", "озноб", "слабост", "кашел", "кашель", "просту", "грипп", "давлен"],
    "ЛОР": ["нос", "горл", "ух", "отит", "гайморит", "насморк", "ангин"],
    "Окулист": ["глаз", "зрен", "близорук", "линз", "конъюнктивит"],
    "Хирург": ["перелом", "ушиб", "ран", "вывих", "растяжен", "ожог"],
    "Кардиолог": ["сердц", "аритм", "тахикард", "груд", "одышк", "пульс"],
    "Невролог": ["головокружен", "мигрен", "спин", "поясниц", "онемен", "судорог"],
    "Дерматолог": ["сып", "прыщ", "зуд", "экзем", "псориаз", "кож"],
}


def phone_digits(value):
    text = str(value or "").strip()
    digits = "".join(char for char in text if char in "0123456789")
    if text.startswith("+7") and digits.startswith("7"):
        return digits[1:]
    if len(digits) == 11 and digits[0] in "78":
        return digits[1:]
    if digits == "7" and text.startswith("+7"):
        return ""
    return digits


def format_phone(value):
    digits = phone_digits(value)[:10]
    result = "+7"
    if digits:
        result += " " + digits[:3]
    if len(digits) > 3:
        result += " " + digits[3:6]
    if len(digits) > 6:
        result += "-" + digits[6:8]
    if len(digits) > 8:
        result += "-" + digits[8:10]
    return result


def normalize_phone(value, required=True):
    digits = phone_digits(value)
    if not digits:
        if required:
            raise ValueError("Введите 10 цифр после +7")
        return ""
    if len(digits) != 10:
        raise ValueError("Введите 10 цифр после +7")
    return "+7" + digits


def password_hash(password):
    salt = secrets.token_bytes(16)
    result = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${result.hex()}"


def check_password(password, saved):
    if saved.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, stored = saved.split("$", 3)
            calculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
            return hmac.compare_digest(calculated, bytes.fromhex(stored))
        except (ValueError, TypeError):
            return False
    if saved.startswith("pbkdf2:"):
        try:
            _, salt, digest = saved.split(":", 2)
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
            return hmac.compare_digest(candidate, bytes.fromhex(digest))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), saved)


def user_dict(user):
    return {
        "login": user.login,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,
        "specialization": user.specialization,
    }


def appointment_dict(a):
    return {
        "id": a.id,
        "patient_login": a.patient_login,
        "name": a.name,
        "phone": a.phone,
        "doctor_login": a.doctor_login,
        "doctor_name": a.doctor_name,
        "doctor": a.doctor,
        "date": a.day,
        "time": a.time,
        "complaints": a.complaints,
        "status": a.status,
        "doctor_notes": a.doctor_notes,
        "attended": a.attended,
        "rated": a.rated,
    }


def review_dict(r):
    return {
        "id": r.id,
        "doctor": r.doctor,
        "doctor_login": r.doctor_login,
        "rating": r.rating,
        "comment": r.comment,
        "date": r.day,
        "complaint": r.complaint,
        "complaint_reason": r.complaint_reason,
        "removed": r.removed,
    }


class ClinicService:
    def __init__(self, folder=None):
        self.db = Database(folder)
        self._init_specializations()
        self._import_legacy()

    def _init_specializations(self):
        with self.db.clinic.begin() as session:
            if session.scalar(select(func.count()).select_from(Specialization)) == 0:
                session.add_all(Specialization(name=name) for name in SPECIALIZATIONS)

    def _import_legacy(self):
        folder = self.db.accounts_engine.url.database
        path = Path(folder).parent
        users_path = path / "users.json"
        appointments_path = path / "appointments.json"
        reviews_path = path / "reviews.json"
        spec_path = path / "specializations.json"
        with self.db.accounts() as session:
            existing = session.scalar(select(func.count()).select_from(User))
        if existing or not users_path.exists():
            return
        try:
            users = json.loads(users_path.read_text(encoding="utf-8"))
            appointments = json.loads(appointments_path.read_text(encoding="utf-8")) if appointments_path.exists() else []
            reviews = json.loads(reviews_path.read_text(encoding="utf-8")) if reviews_path.exists() else []
            specs = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else []
            if isinstance(reviews, dict):
                reviews = reviews.get("reviews", [])
            if not isinstance(users, dict) or not isinstance(appointments, list) or not isinstance(reviews, list):
                return
        except (OSError, ValueError, TypeError):
            return
        with self.db.accounts.begin() as session:
            for login, data in users.items():
                if isinstance(data, dict) and data.get("password_hash"):
                    session.add(User(
                        login=login, password_hash=data["password_hash"], name=data.get("name", login),
                        phone=data.get("phone", ""), role=data.get("role", "patient"),
                        specialization=data.get("specialization", ""),
                        phone_confirmed=bool(data.get("phone_confirmed", False)),
                    ))
        with self.db.clinic.begin() as session:
            for spec in specs if isinstance(specs, list) else []:
                if isinstance(spec, str) and spec.strip():
                    session.merge(Specialization(name=spec.strip()))
            for row in appointments:
                if not isinstance(row, dict) or not row.get("date") or not row.get("time"):
                    continue
                session.add(Appointment(
                    id=row.get("id") or secrets.token_hex(12), patient_login=row.get("patient_login", ""),
                    name=row.get("name", ""), phone=row.get("phone", ""),
                    doctor_login=row.get("doctor_login") or None, doctor_name=row.get("doctor_name", ""),
                    doctor=row.get("doctor", ""), day=row["date"], time=row["time"],
                    complaints=row.get("complaints", ""), status=row.get("status", "active"),
                    doctor_notes=row.get("doctor_notes", ""), attended=row.get("attended"),
                    rated=bool(row.get("rated", False)),
                ))
            for row in reviews:
                if not isinstance(row, dict):
                    continue
                session.add(Review(
                    id=row.get("id") or secrets.token_hex(12), doctor=row.get("doctor", ""),
                    doctor_login=row.get("doctor_login") or None, rating=int(row.get("rating", 5)),
                    comment=row.get("comment", ""), day=row.get("date", date.today().isoformat()),
                    complaint=bool(row.get("complaint", False)),
                    complaint_reason=row.get("complaint_reason", ""), removed=bool(row.get("removed", False)),
                ))

    def has_users(self):
        with self.db.accounts() as session:
            return session.scalar(select(func.count()).select_from(User)) > 0

    def add_user(self, login, password, name, phone, role="patient", specialization="", actor=None):
        login = login.strip()
        name = name.strip()
        if not login or len(login) > 80 or not name:
            raise ValueError("Введите логин и ФИО")
        if len(password) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        if role not in ("patient", "doctor", "registrator"):
            raise ValueError("Неизвестная роль")
        if role != "patient":
            if not (role == "registrator" and not self.has_users()) and (not actor or actor.get("role") != "registrator"):
                raise ValueError("Создавать сотрудников может только регистратор")
        phone = normalize_phone(phone, required=role == "patient")
        with self.db.accounts.begin() as session:
            if session.get(User, login):
                raise ValueError("Логин уже занят")
            session.add(User(
                login=login, password_hash=password_hash(password), name=name, phone=phone,
                role=role, specialization=specialization if role == "doctor" else "",
            ))
        return login

    def authenticate(self, login, password):
        with self.db.accounts.begin() as session:
            user = session.get(User, login.strip())
            if not user or not check_password(password, user.password_hash):
                raise ValueError("Неверный логин или пароль")
            if not user.password_hash.startswith("pbkdf2_sha256$"):
                user.password_hash = password_hash(password)
            return user_dict(user)

    def remember(self, user):
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        with self.db.accounts.begin() as session:
            session.add(LoginSession(token_hash=digest, user_login=user["login"], expires_at=expiry))
        return token

    def restore(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db.accounts.begin() as session:
            saved = session.get(LoginSession, digest)
            if not saved:
                return None
            if saved.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
                session.delete(saved)
                return None
            user = session.get(User, saved.user_login)
            return user_dict(user) if user else None

    def forget(self, token):
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with self.db.accounts.begin() as session:
                saved = session.get(LoginSession, digest)
                if saved:
                    session.delete(saved)

    def change_password(self, user, old_password, new_password):
        if len(new_password) < 8:
            raise ValueError("Новый пароль должен быть не короче 8 символов")
        with self.db.accounts.begin() as session:
            record = session.get(User, user["login"])
            if not record or not check_password(old_password, record.password_hash):
                raise ValueError("Текущий пароль неверен")
            record.password_hash = password_hash(new_password)
            for active in session.scalars(select(LoginSession).where(LoginSession.user_login == record.login)):
                session.delete(active)

    @property
    def specializations(self):
        with self.db.clinic() as session:
            return list(session.scalars(select(Specialization.name).order_by(Specialization.name)))

    def add_specialization(self, name, actor):
        if actor.get("role") != "registrator":
            raise ValueError("Недостаточно прав")
        name = name.strip()
        if not name:
            raise ValueError("Укажите специальность")
        with self.db.clinic.begin() as session:
            if session.get(Specialization, name):
                raise ValueError("Такая специальность уже существует")
            session.add(Specialization(name=name))

    def patients(self):
        with self.db.accounts() as session:
            return [user_dict(u) for u in session.scalars(select(User).where(User.role == "patient").order_by(User.name))]

    def doctors(self, specialization=""):
        with self.db.accounts() as session:
            query = select(User).where(User.role == "doctor")
            if specialization:
                query = query.where(User.specialization == specialization)
            return [(u.login, user_dict(u)) for u in session.scalars(query.order_by(User.name))]

    def get_doctor(self, login):
        with self.db.accounts() as session:
            u = session.get(User, login)
            return user_dict(u) if u and u.role == "doctor" else None

    def recommend(self, text):
        text = text.lower().strip()
        if len(text) < 3:
            return ""
        scores = {spec: sum(word in text for word in words) for spec, words in SYMPTOMS.items()}
        return max(scores, key=scores.get) if max(scores.values(), default=0) > 0 else ""

    def slots(self, doctor_login, day):
        doctor = self.get_doctor(doctor_login)
        if not doctor:
            return []
        try:
            selected = date.fromisoformat(day)
        except ValueError:
            return []
        if selected < date.today() or selected.weekday() > 4 or selected > date.today() + timedelta(days=56):
            return []
        with self.db.clinic() as session:
            busy_query = select(Appointment.time).where(
                Appointment.day == day,
                Appointment.status != "cancelled",
                or_(Appointment.doctor_login == doctor_login,
                    and_(Appointment.doctor_login.is_(None), Appointment.doctor == doctor["specialization"])),
            )
            occupied = set(session.scalars(busy_query))
        return [t for t in SLOTS if t not in occupied and datetime.fromisoformat(f"{day}T{t}") > datetime.now()]

    def book(self, patient_login, doctor_login, day, time, complaints=""):
        with self.db.accounts() as accounts:
            patient = accounts.get(User, patient_login)
            doctor = accounts.get(User, doctor_login) if doctor_login else None
            if not patient or patient.role != "patient":
                raise ValueError("Выберите пациента")
            if not doctor or doctor.role != "doctor":
                raise ValueError("Выберите врача")
            patient_info = user_dict(patient)
            doctor_info = user_dict(doctor)
        if time not in self.slots(doctor_login, day):
            raise ValueError("Выбранное время занято или недоступно")
        record = Appointment(
            id=secrets.token_hex(12), patient_login=patient_login, name=patient_info["name"],
            phone=patient_info["phone"], doctor_login=doctor_login, doctor=doctor_info["specialization"],
            doctor_name=doctor_info["name"], day=day, time=time,
            complaints=complaints.strip()[:5000], status="active",
        )
        try:
            with self.db.clinic.begin() as session:
                session.add(record)
                session.flush()
        except IntegrityError:
            raise ValueError("Это время только что заняли. Выберите другое") from None
        return appointment_dict(record)

    def _visible_filter(self, user):
        role = user["role"]
        if role == "registrator":
            return True
        if role == "patient":
            return Appointment.patient_login == user["login"]
        if role == "doctor":
            return or_(Appointment.doctor_login == user["login"],
                       and_(Appointment.doctor_login.is_(None), Appointment.doctor == user.get("specialization")))
        return False

    def visible_appointments(self, user):
        with self.db.clinic() as session:
            query = select(Appointment).where(self._visible_filter(user)).order_by(Appointment.day, Appointment.time)
            return [appointment_dict(a) for a in session.scalars(query)]

    def update_appointment(self, user, apt_id, action, value=None):
        with self.db.clinic.begin() as session:
            apt = session.scalar(select(Appointment).where(Appointment.id == apt_id, self._visible_filter(user)))
            if not apt:
                raise ValueError("Запись не найдена или нет доступа")
            role = user["role"]
            if action == "cancel" and role in ("patient", "registrator") and apt.status in ("active", "redirected"):
                apt.status = "cancelled"
            elif action == "attended" and role == "doctor" and apt.status in ("active", "redirected"):
                apt.attended = bool(value)
                apt.status = "completed" if value else "cancelled"
            elif action == "note" and role == "doctor":
                apt.doctor_notes = str(value)[:5000]
            elif action == "redirect" and role == "doctor" and apt.status in ("active", "redirected"):
                new_login, new_time = value
                if new_login == apt.doctor_login or new_time not in self.slots(new_login, apt.day):
                    raise ValueError("Выберите другого врача и свободное время")
                doctor = self.get_doctor(new_login)
                apt.doctor_login = new_login
                apt.doctor_name = doctor["name"]
                apt.doctor = doctor["specialization"]
                apt.time = new_time
                apt.status = "redirected"
            else:
                raise ValueError("Действие недоступно для этой роли или статуса")
            try:
                session.flush()
            except IntegrityError:
                raise ValueError("Это время уже занято") from None

    def rate(self, user, apt_id, stars, comment):
        if user["role"] != "patient":
            raise ValueError("Недостаточно прав")
        if not 1 <= stars <= 5:
            raise ValueError("Оценка должна быть от 1 до 5")
        with self.db.clinic.begin() as session:
            apt = session.scalar(select(Appointment).where(
                Appointment.id == apt_id, Appointment.patient_login == user["login"]
            ))
            if not apt or apt.status != "completed" or apt.rated:
                raise ValueError("Этот приём нельзя оценить")
            session.add(Review(
                id=secrets.token_hex(12), doctor=apt.doctor, doctor_login=apt.doctor_login,
                rating=stars, comment=comment.strip() or "Без комментария", day=date.today().isoformat(),
            ))
            apt.rated = True

    def _review_filter(self, user):
        if user["role"] == "registrator":
            return True
        if user["role"] == "doctor":
            return or_(Review.doctor_login == user["login"],
                       and_(Review.doctor_login.is_(None), Review.doctor == user.get("specialization")))
        return Review.removed.is_(False)

    def review_list(self, user):
        with self.db.clinic() as session:
            query = select(Review).where(self._review_filter(user)).order_by(Review.day.desc())
            return [review_dict(r) for r in session.scalars(query)]

    def update_review(self, user, review_id, action, reason=""):
        with self.db.clinic.begin() as session:
            review = session.scalar(select(Review).where(Review.id == review_id, self._review_filter(user)))
            if not review:
                raise ValueError("Отзыв не найден")
            if user["role"] == "doctor" and action == "complain" and not review.complaint and not review.removed:
                if not reason.strip():
                    raise ValueError("Укажите причину")
                review.complaint = True
                review.complaint_reason = reason.strip()
            elif user["role"] == "registrator" and action in ("keep", "remove") and review.complaint:
                review.complaint = False
                review.complaint_reason = ""
                review.removed = action == "remove"
            else:
                raise ValueError("Недостаточно прав или действие недоступно")

    def edit_doctor(self, actor, login, name, phone, specialization, password=""):
        if actor["role"] != "registrator":
            raise ValueError("Недостаточно прав")
        with self.db.accounts.begin() as session:
            user = session.get(User, login)
            if not user or user.role != "doctor":
                raise ValueError("Врач не найден")
            if not name.strip() or not specialization:
                raise ValueError("Введите ФИО и специальность")
            user.name = name.strip()
            user.phone = normalize_phone(phone, required=False)
            user.specialization = specialization
            if password:
                if len(password) < 8:
                    raise ValueError("Пароль должен быть не короче 8 символов")
                user.password_hash = password_hash(password)
                for active in session.scalars(select(LoginSession).where(LoginSession.user_login == login)):
                    session.delete(active)

    def remove_doctor(self, actor, login):
        if actor["role"] != "registrator":
            raise ValueError("Недостаточно прав")
        with self.db.clinic() as session:
            busy = session.scalar(select(func.count()).select_from(Appointment).where(
                Appointment.doctor_login == login, Appointment.status.in_(["active", "redirected"])
            ))
        if busy:
            raise ValueError("У врача есть активные записи. Сначала перенесите их")
        with self.db.accounts.begin() as session:
            user = session.get(User, login)
            if not user or user.role != "doctor":
                raise ValueError("Врач не найден")
            session.delete(user)

    def stats(self, user):
        if user["role"] != "registrator":
            raise ValueError("Недостаточно прав")
        with self.db.clinic() as clinic:
            total = clinic.scalar(select(func.count()).select_from(Appointment))
            today = clinic.scalar(select(func.count()).select_from(Appointment).where(Appointment.day == date.today().isoformat()))
            statuses = dict(clinic.execute(select(Appointment.status, func.count()).group_by(Appointment.status)).all())
        with self.db.accounts() as accounts:
            patients = accounts.scalar(select(func.count()).select_from(User).where(User.role == "patient"))
            doctors = accounts.scalar(select(func.count()).select_from(User).where(User.role == "doctor"))
        return {"total": total, "today": today, "statuses": statuses, "patients": patients, "doctors": doctors}
