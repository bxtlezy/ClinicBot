from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)


class AccountsBase(DeclarativeBase):
    pass


class ClinicBase(DeclarativeBase):
    pass


class User(AccountsBase):
    __tablename__ = "users"

    login: Mapped[str] = mapped_column(String(80), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(250), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[str] = mapped_column(String(25), default="patient", nullable=False)
    specialization: Mapped[str] = mapped_column(String(100), default="")
    phone_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginSession(AccountsBase):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_login: Mapped[str] = mapped_column(ForeignKey("users.login", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Appointment(ClinicBase):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    patient_login: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(50), default="")
    doctor_login: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    doctor_name: Mapped[str] = mapped_column(String(200), default="")
    doctor: Mapped[str] = mapped_column(String(100), default="")
    day: Mapped[str] = mapped_column(String(10), index=True)
    time: Mapped[str] = mapped_column(String(5))
    complaints: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(25), default="active")
    doctor_notes: Mapped[str] = mapped_column(Text, default="")
    attended: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rated: Mapped[bool] = mapped_column(Boolean, default=False)


Index(
    "unique_occupied_doctor_slot",
    Appointment.doctor_login,
    Appointment.day,
    Appointment.time,
    unique=True,
    sqlite_where=Appointment.status != "cancelled",
)


class Review(ClinicBase):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doctor: Mapped[str] = mapped_column(String(100), default="")
    doctor_login: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    day: Mapped[str] = mapped_column(String(10))
    complaint: Mapped[bool] = mapped_column(Boolean, default=False)
    complaint_reason: Mapped[str] = mapped_column(Text, default="")
    removed: Mapped[bool] = mapped_column(Boolean, default=False)


class Specialization(ClinicBase):
    __tablename__ = "specializations"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)


def connect(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    return engine


class Database:
    def __init__(self, folder=None):
        root = Path(folder) if folder else DATA
        root.mkdir(parents=True, exist_ok=True)
        self.accounts_engine = connect(root / "accounts.db")
        self.clinic_engine = connect(root / "clinic.db")
        AccountsBase.metadata.create_all(self.accounts_engine)
        ClinicBase.metadata.create_all(self.clinic_engine)
        self.accounts = sessionmaker(self.accounts_engine, expire_on_commit=False)
        self.clinic = sessionmaker(self.clinic_engine, expire_on_commit=False)

    def close(self):
        self.accounts_engine.dispose()
        self.clinic_engine.dispose()
