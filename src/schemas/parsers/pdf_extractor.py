import pdfplumber


class PDFExtractor:
    def __init__(self, pdf):
        self.pdf = pdf

    def extract(self) -> list[str]:
        text = ""
        with pdfplumber.open(self.pdf) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""

                if "cid:" in page_text:
                    print(f"[PDF] {self.pdf} página {i} contém texto não extraível")
                    return ""

                if not page_text.strip():
                    print(f"[PDF] {self.pdf} página {i} está vazia")
                    return ""

                text += page_text + "\n"
        return text.split("\n")
