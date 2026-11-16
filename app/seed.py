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


INSTITUTIONS += [
    ("LIS", "Liberia Immigration Service", "Residence permits and border services"),
    ("MOL", "Ministry of Labour", "Work permits and labour compliance"),
    ("MOE", "Ministry of Education", "Academic credentials"),
    ("NASSCORP", "National Social Security and Welfare Corporation", "Social security registration"),
    ("MIA", "Ministry of Internal Affairs", "Marriage certificates and local governance"),
    ("MOCI", "Ministry of Commerce and Industry", "Import permits and trade"),
    ("MPW", "Ministry of Public Works", "Construction permits"),
    ("EPA", "Environmental Protection Agency", "Environmental permits"),
    ("LWSC", "Liberia Water and Sewer Corporation", "Water connections"),
]

SERVICES += [
    ("DEATH_CERT", "Death Certificate", "Civil", "MOH", 3.0, 7, False, False,
     ["Medical or burial notification", "Informant's ID"],
     [F("deceased_name", "Full name of the deceased"), F("date_of_death", "Date of death", "date"),
      F("place_of_death", "Place of death"), F("informant_relation", "Your relationship to the deceased")],
     "Register a death and receive the certificate."),
    ("BIRTH_CERT_REPLACE", "Birth Certificate - Replacement", "Civil", "MOH", 3.0, 5, False, False,
     ["Any ID or affidavit"], [F("full_name", "Full name on certificate"), F("dob", "Date of birth", "date"),
                               F("mother_name", "Mother's name")],
     "Replace a lost or damaged birth certificate."),
    ("MARRIAGE_CERT", "Marriage Certificate", "Civil", "MIA", 20.0, 7, True, False,
     ["ID of both spouses", "Proof of ceremony or affidavit"],
     [F("spouse_one", "Full name of first spouse"), F("spouse_two", "Full name of second spouse"),
      F("marriage_date", "Date of marriage", "date"), F("marriage_type", "Type", "select",
                                                        options=["Civil", "Traditional", "Religious"])],
     "Register a marriage and receive the certificate."),
    ("RESIDENT_PERMIT", "Resident Permit", "Immigration", "LIS", 75.0, 14, True, True,
     ["Passport bio page", "Proof of address", "Passport-size photo"],
     [F("full_name", "Full name"), F("nationality", "Nationality"), F("passport_no", "Passport number"),
      F("purpose", "Purpose of stay", "select", options=["Work", "Family", "Study", "Investment"])],
     "Apply for or renew a resident permit."),
    ("WORK_PERMIT", "Work Permit", "Labour", "MOL", 100.0, 14, True, False,
     ["Employment contract", "Passport bio page", "Employer's business registration"],
     [F("full_name", "Employee full name"), F("employer", "Employer"), F("position", "Position"),
      F("nationality", "Nationality")], "Work permit for a foreign national."),
    ("NASSCORP_REG", "Social Security Registration", "Social Security", "NASSCORP", 0.0, 5, False, False,
     ["National ID"], [F("full_name", "Full name"), F("nin", "National ID number"),
                       F("employer", "Employer", required=False)],
     "Register for a social security number."),
    ("BUSINESS_RENEW", "Business Registration - Annual Renewal", "Business", "LBR", 50.0, 3, False, False,
     ["Current registration certificate", "Tax clearance"],
     [F("business_name", "Business name"), F("reg_no", "Registration number")],
     "Renew your business registration for another year."),
    ("IMPORT_PERMIT", "Import Permit Declaration", "Business", "MOCI", 30.0, 7, False, False,
     ["Business registration", "Pro-forma invoice"],
     [F("business_name", "Business name"), F("goods", "Goods to import"), F("origin", "Country of origin"),
      F("value_usd", "Declared value (USD)", "number")], "Permit to import goods."),
    ("CONSTRUCTION_PERMIT", "Construction Permit", "Land", "MPW", 60.0, 21, False, False,
     ["Land deed", "Architectural drawings"],
     [F("site_address", "Site address"), F("building_use", "Use", "select",
                                           options=["Residential", "Commercial", "Mixed"]),
      F("floors", "Number of floors", "number")], "Permit to build or extend a structure."),
    ("LAND_DEED_REG", "Land Deed Registration", "Land", "LLA", 50.0, 21, True, False,
     ["Deed or sale agreement", "Survey plan", "National ID"],
     [F("county", "County"), F("district", "District"), F("area", "Area (acres or lots)"),
      F("acquired_from", "Acquired from")], "Register a deed with the Land Authority."),
    ("EPA_PERMIT", "Environmental Permit", "Environment", "EPA", 100.0, 30, False, False,
     ["Project brief", "Site map"], [F("project_name", "Project name"), F("location", "Location"),
                                     F("sector", "Sector", "select",
                                       options=["Mining", "Agriculture", "Construction", "Energy", "Other"])],
     "Environmental permit for a project."),
    ("WATER_CONNECT", "Water Connection", "Utilities", "LWSC", 35.0, 14, False, False,
     ["National ID", "Proof of address"], [F("address", "Service address"),
                                           F("property_type", "Property", "select",
                                             options=["Residential", "Commercial"])],
     "Request a new water connection."),
    ("CREDENTIAL_VERIFY", "Academic Credential Verification", "Education", "MOE", 10.0, 7, False, False,
     ["Certificate or diploma", "Transcript"],
     [F("full_name", "Name on the credential"), F("school", "School or institution"),
      F("year", "Year completed", "number")], "Official verification of a school or university credential."),
    ("DRIVER_LICENSE_REPLACE", "Driver's License - Replacement", "Transport", "MOT", 15.0, 5, True, False,
     ["Police report if lost"], [F("license_no", "License number", required=False), F("full_name", "Full name"),
                                 F("reason", "Reason", "select", options=["Lost", "Damaged", "Stolen"])],
     "Replace a lost, stolen or damaged license."),
    ("VEHICLE_REG_RENEW", "Vehicle Registration - Renewal", "Transport", "MOT", 20.0, 3, False, False,
     ["Current registration", "Insurance certificate"], [F("plate_no", "Plate number")],
     "Renew your vehicle registration."),
    ("TAX_RETURN", "Personal Income Tax Return", "Tax", "LRA", 0.0, 3, False, False,
     ["Income statement"], [F("tin", "TIN"), F("tax_year", "Tax year", "number"),
                            F("income_usd", "Total income (USD)", "number")], "File your annual income tax return."),
    ("LOST_DOC_REPORT", "Lost Document Police Report", "Legal", "LNP", 0.0, 2, False, False,
     ["Any ID"], [F("document_type", "Document lost"), F("date_lost", "Date lost", "date"),
                  F("place_lost", "Where it was lost")], "Police report needed to replace a lost document."),
]


def _upgrade_v2(s):
    """Seeded services gain document rules and identity-document questions (never touches edited ones)."""
    from .rules import normalize_doc_rules
    foreigner = s.code in ("RESIDENT_PERMIT", "WORK_PERMIT")
    fields, has_identity = [], False
    for f in s.form_fields or []:
        name = f.get("name")
        if f.get("type") == "identity":
            has_identity = True
        elif name == "nin":
            f = {"name": "nin", "label": "National ID number", "type": "identity", "required": f.get("required", True),
                 "doc_types": ["National ID"], "countries": ["LR"]}
        elif name == "passport_no":
            f = {"name": "passport_no", "label": "Passport", "type": "identity", "required": True,
                 "doc_types": ["Passport"]}
            if not foreigner:
                f["countries"] = ["LR"]
                f["label"] = "Current Liberian passport number"
            has_identity = True
        elif name == "nationality" and foreigner:
            if any(x.get("name") == "passport_no" for x in s.form_fields):
                continue  # the passport question now asks for nationality itself
            f = {"name": "passport_no", "label": "Passport", "type": "identity", "required": True,
                 "doc_types": ["Passport"]}
        fields.append(f)
    s.form_fields = fields
    s.required_documents = normalize_doc_rules(s.required_documents)
    s.seed_version = 2


STAGES = {
    "PASSPORT_NEW": [("Document check", "Confirm the ID and photo meet the rules."),
                     ("Identity verification", "Confirm the face and fingerprint evidence."),
                     ("Issuance approval", "Final check before the passport is printed.")],
    "BUSINESS_REG": [("Name search", "Check the proposed name is available and permitted."),
                     ("Registration approval", "Confirm documents and approve.")],
    "WORK_PERMIT": [("Employer verification", "Confirm the employer is registered and compliant."),
                    ("Permit decision", "Decide on the permit.")],
    "CONSTRUCTION_PERMIT": [("Technical review", "Review the drawings against the building code."),
                            ("Permit approval", "Approve or reject the permit.")],
    "LAND_DEED_REG": [("Records search", "Search for conflicting deeds."),
                      ("Survey validation", "Check the survey plan against the parcel."),
                      ("Registrar sign-off", "Final registration by the administrator.")],
}


def _upgrade_v3(s):
    """Example review stages on a few seeded services; edited services are never touched."""
    if s.code in STAGES and not s.workflow:
        last = len(STAGES[s.code]) - 1
        s.workflow = [{"name": n, "instructions": t, "separate_officer": False,
                       "requires_admin": s.code == "LAND_DEED_REG" and k == last}
                      for k, (n, t) in enumerate(STAGES[s.code])]
    s.seed_version = 3


# months of validity and the service used to renew - illustrative until each agency confirms
VALIDITY = {
    "NATIONAL_ID_NEW": (60, "NATIONAL_ID_RENEW"), "NATIONAL_ID_RENEW": (60, "NATIONAL_ID_RENEW"),
    "PASSPORT_NEW": (60, "PASSPORT_RENEW"), "PASSPORT_RENEW": (60, "PASSPORT_RENEW"),
    "DRIVER_LICENSE_NEW": (60, "DRIVER_LICENSE_RENEW"), "DRIVER_LICENSE_RENEW": (60, "DRIVER_LICENSE_RENEW"),
    "DRIVER_LICENSE_REPLACE": (60, "DRIVER_LICENSE_RENEW"),
    "VEHICLE_REG": (12, "VEHICLE_REG_RENEW"), "VEHICLE_REG_RENEW": (12, "VEHICLE_REG_RENEW"),
    "BUSINESS_REG": (12, "BUSINESS_RENEW"), "BUSINESS_RENEW": (12, "BUSINESS_RENEW"),
    "RESIDENT_PERMIT": (12, "RESIDENT_PERMIT"), "WORK_PERMIT": (12, "WORK_PERMIT"),
    "TAX_CLEARANCE": (12, "TAX_CLEARANCE"), "POLICE_CLEARANCE": (6, "POLICE_CLEARANCE"),
}


def _upgrade_v4(s):
    if s.code in VALIDITY and not s.validity_months:
        s.validity_months, s.renewal_code = VALIDITY[s.code]
    s.seed_version = 4


def seed(reset_demo=True):
    insts = {}
    for code, name, desc in INSTITUTIONS:
        i = Institution.query.filter_by(code=code).first() or Institution(code=code, name=name, description=desc)
        db.session.add(i)
        insts[code] = i
    db.session.flush()
    for code, name, cat, inst, fee, sla, face, finger, docs, fields, desc in SERVICES:
        if Service.query.filter_by(code=code).first():
            continue  # keep whatever an administrator has changed
        s = Service(code=code)
        s.name, s.category, s.institution_id, s.fee_usd, s.sla_days = name, cat, insts[inst].id, fee, sla
        s.requires_face, s.requires_fingerprint, s.required_documents = face, finger, docs
        s.form_fields, s.description = fields, desc
        db.session.add(s)
    db.session.flush()
    for s in Service.query:
        if (s.seed_version or 0) < 2:
            _upgrade_v2(s)
        if (s.seed_version or 0) < 3:
            _upgrade_v3(s)
        if (s.seed_version or 0) < 4:
            _upgrade_v4(s)
    from flask import current_app
    demo = bool(current_app.config.get("DEMO_MODE"))
    # (phone, name, role, institution, password) - public, so only ever active in demo mode
    demo_accounts = [("+231770000001", "System Admin", "admin", None, "Admin@12345"),
                     ("+231770000002", "NIR Officer", "officer", "NIR", "Officer@12345"),
                     ("+231770000003", "MFA Officer", "officer", "MFA", "Officer@12345"),
                     ("+231770000004", "Demo Agent (Paynesville)", "agent", None, "Agent@12345"),
                     ("+231770000005", "NIR Administrator", "inst_admin", "NIR", "InstAdmin@12345")]
    for phone, name, role, inst, pw in demo_accounts:
        u = User.query.filter_by(phone=phone).first()
        if u and u.full_name == name and u.role == role:
            u.is_demo = True  # accounts created by earlier versions of this seed
        if demo and not u:
            db.session.add(User(full_name=name, phone=phone, role=role, is_demo=True, phone_verified=True,
                                institution_id=insts[inst].id if inst else None,
                                password_hash=generate_password_hash(pw)))
    db.session.flush()
    if not demo:
        for u in User.query.filter(User.is_demo.is_(True), User.disabled.isnot(True)):
            u.disabled, u.token_version = True, (u.token_version or 0) + 1
    else:
        for u in User.query.filter(User.is_demo.is_(True), User.disabled.is_(True)):
            u.disabled = False
    _bootstrap_admin(current_app.config)
    db.session.commit()


def _bootstrap_admin(cfg):
    """Create the first real super administrator from BOOTSTRAP_ADMIN_* settings (once)."""
    from .utils import normalize_phone, password_problem
    phone = normalize_phone(cfg.get("BOOTSTRAP_ADMIN_PHONE"))
    pw = cfg.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not phone or not pw or User.query.filter_by(phone=phone).first():
        return
    if password_problem(pw):
        current_app_logger().error("BOOTSTRAP_ADMIN_PASSWORD is too weak; no administrator was created")
        return
    db.session.add(User(full_name=(cfg.get("BOOTSTRAP_ADMIN_NAME") or "Platform Administrator")[:150], phone=phone,
                        role="admin", admin_perms=["all"], phone_verified=True, must_change_password=True,
                        password_hash=generate_password_hash(pw)))


def current_app_logger():
    from flask import current_app
    return current_app.logger
