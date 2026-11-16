"""Seed data: Liberian institutions, an initial service catalog and demo staff accounts.

Fees and SLAs are ILLUSTRATIVE placeholders - replace with the official schedule of each agency.
"""
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import Institution, Service, User

INSTITUTIONS = [
    ("NIR", "National Identification Registry", "National ID card and civil identity"),
    ("BIN", "Bureau of Immigration and Naturalization", "Passports support, visas, residence"),
    ("MFA", "Ministry of Foreign Affairs", "Passports and consular services"),
    ("LRA", "Liberia Revenue Authority", "Tax and customs"),
    ("LBR", "Liberia Business Registry", "Business registration"),
    ("MOT", "Ministry of Transport", "Driver licensing and vehicle registration"),
    ("LNP", "Liberia National Police", "Police clearance"),
    ("MOH", "Ministry of Health", "Births, deaths and health certificates"),
    ("LLA", "Liberia Land Authority", "Land records and deeds"),
    ("LEC", "Liberia Electricity Corporation", "Electricity connections"),
]

ID_DOCS = ["Passport-size photo", "Proof of address"]
P = {"name": "full_name", "label": "Full name", "type": "text", "required": True}


def F(name, label, type="text", required=True, options=None):
    f = {"name": name, "label": label, "type": type, "required": required}
    if options:
        f["options"] = options
    return f


SERVICES = [
    # code, name, category, inst, fee, sla, face, finger, docs, fields, description
    ("NATIONAL_ID_NEW", "National ID Card - New", "Identity", "NIR", 5.0, 10, True, True,
     ["Birth certificate or affidavit", "Passport-size photo"],
     [F("full_name", "Full name"), F("dob", "Date of birth", "date"), F("place_of_birth", "Place of birth"),
      F("county", "County of residence"), F("father_name", "Father's name", required=False),
      F("mother_name", "Mother's name")],
     "Apply for your first biometric National ID card."),
    ("NATIONAL_ID_RENEW", "National ID Card - Renewal / Replacement", "Identity", "NIR", 5.0, 7, True, False,
     ["Old ID or police report if lost"],
     [F("nin", "National ID number"), F("reason", "Reason", "select", options=["Expired", "Lost", "Damaged"])],
     "Renew an expired card or replace a lost or damaged one."),
    ("PASSPORT_NEW", "Passport - New (Ordinary)", "Travel", "MFA", 50.0, 15, True, True,
     ["National ID or birth certificate", "Passport-size photo"],
     [F("full_name", "Full name"), F("dob", "Date of birth", "date"), F("nin", "National ID number", required=False),
      F("travel_reason", "Reason for passport", required=False)],
     "Apply for a new ordinary Liberian passport."),
    ("PASSPORT_RENEW", "Passport - Renewal", "Travel", "MFA", 50.0, 10, True, False,
     ["Expiring passport"], [F("passport_no", "Current passport number"), F("dob", "Date of birth", "date")],
     "Renew an existing passport."),
    ("DRIVER_LICENSE_NEW", "Driver's License - New", "Transport", "MOT", 25.0, 7, True, False,
     ["National ID", "Medical certificate"],
     [F("full_name", "Full name"), F("license_class", "Class", "select", options=["A", "B", "C", "D"]),
      F("dob", "Date of birth", "date")], "Apply for a driver's license (booking of road test follows)."),
    ("DRIVER_LICENSE_RENEW", "Driver's License - Renewal", "Transport", "MOT", 15.0, 5, False, False,
     ["Existing license"], [F("license_no", "License number")], "Renew your driver's license online."),
    ("VEHICLE_REG", "Vehicle Registration", "Transport", "MOT", 30.0, 7, False, False,
     ["Proof of ownership", "Customs clearance"],
     [F("plate_or_vin", "VIN / chassis number"), F("make", "Make"), F("model", "Model"), F("year", "Year", "number")],
     "Register a vehicle and get plates."),
    ("TIN_REG", "Tax Identification Number (TIN)", "Tax", "LRA", 0.0, 3, False, False,
     ["National ID"], [F("full_name", "Full name"), F("nin", "National ID number")],
     "Get a TIN for personal tax and business use."),
    ("TAX_CLEARANCE", "Tax Clearance Certificate", "Tax", "LRA", 10.0, 5, False, False,
     ["TIN certificate"], [F("tin", "TIN"), F("tax_year", "Tax year", "number")],
     "Certificate that your tax obligations are current."),
    ("BUSINESS_REG", "Business Registration", "Business", "LBR", 100.0, 5, True, False,
     ["Owner ID", "Memorandum / business plan"],
     [F("business_name", "Proposed business name"), F("business_type", "Type", "select",
        options=["Sole proprietorship", "Partnership", "Limited company", "NGO"]),
      F("activity", "Main activity"), F("address", "Business address")],
     "Register a new business with the LBR."),
    ("POLICE_CLEARANCE", "Police Clearance Certificate", "Legal", "LNP", 10.0, 5, True, True,
     ["National ID"], [F("full_name", "Full name"), F("purpose", "Purpose", "select",
                                                      options=["Employment", "Travel", "Visa", "Other"])],
     "Certificate of good conduct."),
    ("BIRTH_CERT", "Birth Certificate", "Civil", "MOH", 3.0, 7, False, False,
     ["Hospital notification or affidavit", "Parent's ID"],
     [F("child_name", "Child's full name"), F("dob", "Date of birth", "date"), F("place_of_birth", "Place of birth"),
      F("father_name", "Father's name", required=False), F("mother_name", "Mother's name")],
     "Register a birth and get the certificate."),
    ("LAND_SEARCH", "Land Title Search", "Land", "LLA", 15.0, 7, False, False,
     ["Property description"], [F("county", "County"), F("district", "District"),
                                F("parcel", "Parcel / deed number", required=False)],
     "Check ownership records for a land parcel."),
    ("ELEC_CONNECT", "Electricity Connection", "Utilities", "LEC", 40.0, 14, False, False,
     ["National ID", "Proof of address"], [F("address", "Service address"), F("meter_type", "Meter", "select",
                                                                              options=["Prepaid", "Postpaid"])],
     "Request a new electricity connection."),
]


def seed(reset_demo=True):
    insts = {}
    for code, name, desc in INSTITUTIONS:
        i = Institution.query.filter_by(code=code).first() or Institution(code=code, name=name, description=desc)
        db.session.add(i)
        insts[code] = i
    db.session.flush()
    for code, name, cat, inst, fee, sla, face, finger, docs, fields, desc in SERVICES:
        s = Service.query.filter_by(code=code).first() or Service(code=code)
        s.name, s.category, s.institution_id, s.fee_usd, s.sla_days = name, cat, insts[inst].id, fee, sla
        s.requires_face, s.requires_fingerprint, s.required_documents = face, finger, docs
        s.form_fields, s.description = fields, desc
        db.session.add(s)
    if reset_demo and not User.query.filter_by(role="admin").first():
        db.session.add(User(full_name="System Admin", phone="+231770000001", role="admin",
                            password_hash=generate_password_hash("Admin@12345"), phone_verified=True))
        for code, phone in (("NIR", "+231770000002"), ("MFA", "+231770000003")):
            db.session.add(User(full_name=f"{code} Officer", phone=phone, role="officer",
                                institution_id=insts[code].id,
                                password_hash=generate_password_hash("Officer@12345"), phone_verified=True))
    from flask import current_app
    if current_app.config.get("EXPOSE_OTP") and not User.query.filter_by(role="agent").first():
        # demo mode only: a sample agent so the agent desk can be tried
        db.session.add(User(full_name="Demo Agent (Paynesville)", phone="+231770000004", role="agent",
                            password_hash=generate_password_hash("Agent@12345"), phone_verified=True))
    db.session.commit()
