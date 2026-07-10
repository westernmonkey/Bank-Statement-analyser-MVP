"""Flask HTTP routes for the fundability analyser."""

from flask import Flask, jsonify, request, send_from_directory

from app import STATIC_DIR
from app.parsing.banks import bank_display_name, detect_bank
from app.parsing.blocks import extract_raw_blocks
from app.parsing.company import extract_company_profile, extract_statement_period
from app.parsing.headers import extract_transaction_blocks, parse_header
from app.parsing.pdf import extract_pages
from app.parsing.transactions import parse_transactions
from app.scoring.score import compute_score
from app.services.admin_log import append_admin_record
from app.services.analyser import call_analyser
from app.services.telegram_notify import send_analysis_telegram


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "linkit-analyser.html")

    @app.route("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(STATIC_DIR / "assets", filename)

    @app.route("/analyse", methods=["POST"])
    def analyse():
        try:
            aecb_raw = request.form.get("aecb", "").strip()
            months_raw = request.form.get("months", "").strip()
            lead_name = request.form.get("name", "").strip()
            lead_phone = request.form.get("phone", "").strip()
            pdf = request.files.get("pdf")

            if not aecb_raw or not months_raw:
                return jsonify({"error": "Missing AECB score or months in business."}), 400
            if not pdf:
                return jsonify({"error": "No PDF file received. Please upload your bank statement."}), 400
            if not pdf.filename.lower().endswith(".pdf"):
                return jsonify({"error": f"File '{pdf.filename}' is not a PDF. Only PDF files are accepted."}), 400

            try:
                aecb = int(aecb_raw)
                months = int(months_raw)
            except ValueError:
                return jsonify({"error": "AECB score and months must be whole numbers."}), 400

            if not (300 <= aecb <= 900):
                return jsonify({"error": f"AECB score {aecb} is out of range. Must be between 300 and 900."}), 400
            if months < 1:
                return jsonify({"error": "Months in business must be at least 1."}), 400

            try:
                pages = extract_pages(pdf)
            except Exception as e:
                return jsonify({"error": f"Could not read PDF: {str(e)}. Make sure the file is not password protected."}), 400

            if len(pages) < 1:
                return jsonify({"error": "PDF appears to be empty or unreadable."}), 400

            full_text = "\n".join(pages)
            if not full_text.strip():
                return jsonify({
                    "error": "This PDF has no selectable text — it looks like a scanned image. "
                             "Please upload a digital PDF exported from your bank (not a photo scan)."
                }), 400

            bank = detect_bank(full_text, pages[0])

            raw_blocks = extract_raw_blocks(pages, bank)
            blocks = extract_transaction_blocks(pages, bank)
            header = parse_header(full_text, pages, bank, raw_blocks)

            if header.get("total_credits_6m", 0) == 0 and header.get("closing_balance", 0) == 0:
                return jsonify({
                    "error": "Could not extract financial data from this statement. "
                             "Make sure the PDF is a complete, unprotected bank statement with selectable text."
                }), 400

            transactions = parse_transactions(
                blocks, header["total_credits_6m"], bank=bank, raw_blocks=raw_blocks,
            )

            metrics = {
                "bank": bank_display_name(bank, full_text),
                "aecb": aecb,
                "months_in_business": months,
                **header,
                **transactions,
            }

            score = compute_score(metrics)

            result, model_used = call_analyser(metrics, score)

            company_profile = extract_company_profile(full_text, pages, bank)
            statement_period = extract_statement_period(full_text, pages, bank)
            admin_payload = {
                "company_profile": company_profile,
                "parser": bank,
                "bank_label": metrics["bank"],
                "source_filename": pdf.filename,
                "score": score,
                "verdict": result.get("verdict", "Moderate"),
                "sub": result.get("sub", ""),
                "points": result.get("points", []),
                "model_used": model_used,
                "metrics": metrics,
                "aecb_input": aecb,
                "months_in_business_input": months,
                "lead_name": lead_name,
                "lead_phone": lead_phone,
            }
            try:
                append_admin_record(admin_payload)
            except Exception:
                pass
            try:
                send_analysis_telegram(admin_payload)
            except Exception:
                pass

            return jsonify({
                "score": score,
                "metrics": metrics,
                "verdict": result.get("verdict", "Moderate"),
                "sub": result.get("sub", ""),
                "points": result.get("points", []),
                "model_used": model_used,
                "company_name": company_profile.get("name", ""),
                "statement_period": statement_period.get("display", ""),
            })

        except Exception as e:
            return jsonify({
                "error": f"Unexpected server error: {str(e)}. Please try again or contact support."
            }), 500
