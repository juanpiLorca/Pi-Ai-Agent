from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from pi_core.mail.redact import redact_raw, redact_text


def test_redact_text():
    text = (
        "Estimado Juan Lorca, RUT 12.345.678-9, compra por $1.234.567 con tarjeta "
        "4111 1111 1111 1234 el 22/10/2026. Cuenta 00-123-45678-90. https://x.cl/t?uid=999"
    )
    out = redact_text(text, ["Juan Lorca"], [])
    assert "Juan" not in out and "Lorca" not in out
    assert "12.345.678-9" not in out
    assert "$1.234.567" in out and "22/10/2026" in out
    assert "XXXXXXXXXXXX1234" in out and "XXXXXXXX7890" in out
    assert "uid=999" not in out and "https://x.cl/t" in out


def test_redact_raw_keeps_text_parts_and_drops_private_headers():
    msg = EmailMessage()
    msg["From"] = "Banco <aviso@bice.cl>"
    msg["To"] = "Juan <juan.lorca@uc.cl>"
    msg["Subject"] = "Compra Juan"
    msg["Received"] = "from internal.host"
    msg["Message-ID"] = "<abc@bice.cl>"
    msg.set_content("Hola juan.lorca@uc.cl")
    msg.add_alternative("<p>Hola <b>JUAN</b></p>", subtype="html")
    msg.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="logo.png")

    out = BytesParser(policy=policy.default).parsebytes(redact_raw(msg.as_bytes(), ["Juan"]))
    assert out["From"] == "Banco <aviso@bice.cl>"
    assert out["Subject"] == "Compra NOMBRE"
    assert out["Received"] is None
    assert out["Message-ID"] != "<abc@bice.cl>"
    assert out.get_body(("plain",)).get_content().strip() == "Hola usuario@example.com"
    assert "NOMBRE" in out.get_body(("html",)).get_content()
    assert not list(out.iter_attachments())
