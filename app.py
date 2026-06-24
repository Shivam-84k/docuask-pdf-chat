import os
import threading
import webbrowser

import fitz
from google import genai
from google.genai import types
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
CORS(app)

MAX_DOCUMENT_CHARS = 10000
MODEL_NAME = "gemini-2.5-flash"
APP_URL = "http://127.0.0.1:5000"


def json_error(message, status_code=400):
    response = jsonify({"error": message})
    response.status_code = status_code
    return response


def extract_pdf_text(file_bytes):
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        pages = [page.get_text("text") for page in document]
    return "\n".join(pages).strip()


def build_prompt(pdf_text, question):
    return (
        "You are a helpful assistant. Answer the user's question using ONLY the "
        "information from the document below. If the answer is not in the document, "
        "say 'I could not find this in the document.'\n\n"
        "Document:\n"
        f"{pdf_text}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing Gemini API key. Set the GEMINI_API_KEY environment variable."
        )
    return genai.Client(api_key=api_key)


@app.errorhandler(413)
def request_entity_too_large(_error):
    return json_error("The PDF is too large. Please upload a file under 16 MB.", 413)


@app.route("/upload", methods=["POST"])
def upload_pdf():
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return json_error("Please choose a PDF file to upload.", 400)

    filename = (uploaded_file.filename or "").strip()
    if not filename:
        return json_error("The selected file is missing a filename.", 400)

    if not filename.lower().endswith(".pdf"):
        return json_error("Only PDF files are supported.", 400)

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return json_error("The uploaded PDF is empty.", 400)

    try:
        extracted_text = extract_pdf_text(file_bytes)
    except Exception:
        return json_error(
            "We couldn't read that PDF. Please try a different file.", 400
        )

    if not extracted_text:
        return json_error(
            "We couldn't extract readable text from this PDF.", 400
        )

    return jsonify({"pdf_text": extracted_text})


@app.route("/", methods=["GET"])
def serve_frontend():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})


@app.route("/ask", methods=["POST"])
def ask_question():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    pdf_text = str(payload.get("pdf_text", "")).strip()

    if not question:
        return json_error("Please enter a question about the document.", 400)

    if not pdf_text:
        return json_error("Upload and process a PDF before asking a question.", 400)

    truncated_text = pdf_text[:MAX_DOCUMENT_CHARS]
    prompt = build_prompt(truncated_text, question)

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=800,
            ),
        )
        answer = response.text
    except RuntimeError as exc:
        return json_error(str(exc), 500)
    except Exception as exc:
        print(f"Exception occurred: {exc}")
        import traceback
        traceback.print_exc()
        return json_error(
            f"We couldn't get an answer from the language model right now. Error: {str(exc)}",
            500,
        )

    if not answer:
        return json_error(
            "The language model returned an empty response. Please try again.",
            500,
        )

    return jsonify({"answer": answer})


if __name__ == "__main__":
    if os.getenv("DOCUASK_AUTO_OPEN") == "1":
        threading.Timer(1.5, lambda: webbrowser.open(APP_URL)).start()

    app.run(host="0.0.0.0", port=5000, debug=False)
