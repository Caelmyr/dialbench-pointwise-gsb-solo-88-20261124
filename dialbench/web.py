"""Connect photo intake, active dial inspection, and immutable saved readings."""

from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Invalid

from . import analysis, demo, frame, model, output
from .store import Library


def payload():
    return model.document(request.get_json(silent=True))


def png(data, filename=None):
    return send_file(BytesIO(data), mimetype="image/png", as_attachment=bool(filename),
                     download_name=filename, max_age=0)


def install(app, root):
    library = Library(root)
    desk = Blueprint("dial", __name__, url_prefix="/dial")

    @app.get("/")
    def home():
        return render_template("dialbench.html")

    @desk.get("/health")
    def health():
        return jsonify(status="ready", service="dialbench")

    @desk.get("/panels")
    def panels():
        return jsonify(library.list())

    @desk.post("/demo")
    def practice():
        return jsonify(demo.create(library, model)), 201

    @desk.post("/panels")
    def upload():
        inputs = {}
        for role in ("a", "b"):
            upload_input = request.files.get(role)
            if upload_input and upload_input.filename:
                filename = model.text(Path(upload_input.filename).name, maximum=255)
                image = frame.decode(upload_input.stream.read(model.MAX_UPLOAD + 1))
                inputs[role] = image, filename
        if "a" not in inputs:
            raise Invalid("Add a primary dial photo.")
        size = inputs["a"][0].size
        if any(image.size != size for image, _ in inputs.values()):
            raise Invalid("Use a reference photo with the same image size and dial position.")
        title = model.text(request.form.get("title"), Path(inputs["a"][1]).stem)
        return jsonify(library.create(title, inputs, model.defaults(*size))), 201

    @desk.get("/panels/<panel_id>")
    def panel(panel_id):
        return jsonify(library.panel(panel_id))

    @desk.get("/panels/<panel_id>/source/<role>.png")
    def source(panel_id, role):
        archive = request.args.get("report")
        record = library.report(panel_id, archive) if archive else library.panel(panel_id)
        path = library.source(record, role)
        return send_file(path, mimetype="image/png", as_attachment="download" in request.args,
                         download_name=f"dial-{role}.png", max_age=0)

    @desk.post("/panels/<panel_id>/prepared")
    def preparation(panel_id):
        chosen = payload()
        project = model.text(chosen.get("project"))
        result = model.text(chosen.get("result"))
        preparation = app.extensions["results"]
        meta = preparation.metadata(project, result)
        if meta["stale"]:
            raise Invalid("Run the preparation again before attaching its result.")
        record = library.panel(panel_id)
        if (meta["width"], meta["height"]) != (record["width"], record["height"]):
            raise Invalid("The prepared reference must retain the primary photo dimensions.")
        path = preparation.image_path(project, "image", result)
        photo = frame.decode(path.read_bytes())
        provenance = {"project": project, "result": result}
        return jsonify(library.set_companion(panel_id, chosen.get("revision"), photo,
                                             model.text(meta["name"], "Prepared photo"), provenance))

    @desk.put("/panels/<panel_id>/settings")
    def update_settings(panel_id):
        chosen = payload()
        record = library.panel(panel_id)
        settings = model.settings(chosen.get("settings"), record)
        return jsonify(library.revise(panel_id, chosen.get("revision"), settings))

    @desk.post("/panels/<panel_id>/preview")
    def preview(panel_id):
        chosen = payload()
        record = library.panel(panel_id)
        settings = model.settings(chosen.get("settings"), record)
        mode = model.mode(chosen.get("mode"))
        planning = bool(chosen.get("plan")) and mode == "arc" and len(record["sources"]) > 1
        return jsonify(analysis.inspect(library, record, settings, mode, planning=planning))

    @desk.post("/panels/<panel_id>/point")
    def point_probe(panel_id):
        chosen = payload()
        archive = chosen.get("report")
        record = library.report(panel_id, archive) if archive else library.panel(panel_id)
        settings = record["settings"] if archive else model.settings(chosen.get("settings"), record)
        role = model.source(chosen.get("source"), record) if chosen.get("source") else None
        return jsonify(analysis.pixel_probe(library, record, settings,
                                            model.point(chosen.get("point"), record), role))

    @desk.post("/panels/<panel_id>/reports")
    def archive(panel_id):
        chosen = payload()
        record = library.panel(panel_id)
        revision = chosen.get("revision")
        library.require_revision(record, revision)
        settings = model.settings(chosen.get("settings"), record)
        report = analysis.inspect(library, record, settings, model.mode(chosen.get("mode")))
        current, saved = library.retain(panel_id, revision, report)
        return jsonify(panel=current, report=saved), 201

    @desk.get("/panels/<panel_id>/reports/<report_id>")
    def retrieve(panel_id, report_id):
        return jsonify(library.report(panel_id, report_id))

    @desk.get("/panels/<panel_id>/reports/<report_id>/report.csv")
    def saved_csv(panel_id, report_id):
        report = library.report(panel_id, report_id)
        headers = {"Content-Disposition": f'attachment; filename="dial-{report["mode"]}.csv"'}
        return Response(output.tables(report), mimetype="text/csv", headers=headers)

    @desk.get("/panels/<panel_id>/reports/<report_id>/overlay.png")
    def saved_overlay(panel_id, report_id):
        report = library.report(panel_id, report_id)
        return png(output.overlay(library, report, report), "dial-overlay.png" if "download" in request.args else None)

    app.register_blueprint(desk)
