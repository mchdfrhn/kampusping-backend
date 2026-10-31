from enum import StrEnum


class PhoneStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"


AUDIT_STUDENT_PHONE_CHANGED = "student.phone_changed"
AUDIT_ENTITY_STUDENT = "student"
PHONE_AUDIT_FIELD = "phone"
PHONE_MAX_LENGTH = 20
PHONE_INPUT_MAX_LENGTH = 50
