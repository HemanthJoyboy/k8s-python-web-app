from flask import Flask

app = Flask(__name__)

VERSION = "1.0"          # <- change this (and the message) when you update the app
MESSAGE = "Hello from my Docker container!"

@app.route("/")
def home():
    return f"""
    <html>
      <body style="font-family: sans-serif; text-align: center; margin-top: 15vh;">
        <h1>{MESSAGE}</h1>
        <p>App version: <b>{VERSION}</b></p>
      </body>
    </html>
    """

@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
