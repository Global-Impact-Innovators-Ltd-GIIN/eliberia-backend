from flask import Blueprint, jsonify, request

from ..models import Institution, Service

bp = Blueprint("catalog", __name__, url_prefix="/api")


@bp.get("/institutions")
def institutions():
    return jsonify([i.to_dict() for i in Institution.query.order_by(Institution.name)])


@bp.get("/services")
def services():
    q = Service.query.filter_by(active=True)
    if request.args.get("category"):
        q = q.filter_by(category=request.args["category"])
    if request.args.get("institution"):
        q = q.join(Institution).filter(Institution.code == request.args["institution"])
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.filter(Service.name.ilike(like) | Service.description.ilike(like))
    items = q.order_by(Service.category, Service.name).all()
    return jsonify([s.to_dict() for s in items])


@bp.get("/services/<code>")
def service(code):
    s = Service.query.filter_by(code=code, active=True).first_or_404()
    return jsonify(s.to_dict())


@bp.get("/categories")
def categories():
    rows = Service.query.with_entities(Service.category).filter_by(active=True).distinct().all()
    return jsonify(sorted(r[0] for r in rows))


@bp.get("/centers")
def centers():
    return jsonify(CENTERS)


CENTERS = ["Monrovia (Capitol Hill) Service Center", "Paynesville Service Center",
           "Gbarnga Service Center", "Buchanan Service Center", "Kakata Service Center",
           "Zwedru Service Center", "Harper Service Center", "Voinjama Service Center"]
