import logging
import time

import pdfplumber

logger = logging.getLogger(__name__)


class PDFExtractor:
    def __init__(self, pdf):
        self.pdf = pdf

    def extract(self) -> tuple[list[str], int]:
        text = ""
        total_paginas = 0
        inicio = time.perf_counter()
        logger.info("Extraindo conteudo do PDF: %s", self.pdf)

        with pdfplumber.open(self.pdf) as pdf:
            total_paginas = len(pdf.pages)

            if total_paginas == 0:
                logger.warning(
                    "PDF sem paginas ou conteudo insuficiente: %s | paginas=0",
                    self.pdf,
                )
                return "", 0

            for i, page in enumerate(pdf.pages, start=1):
                inicio_pagina = time.perf_counter()
                page_text = page.extract_text() or ""

                if "cid:" in page_text:
                    logger.warning(
                        "PDF com texto nao extraivel (cid): %s | pagina=%s",
                        self.pdf, i,
                    )
                    return "", total_paginas

                if not page_text.strip():
                    logger.warning(
                        "PDF com pagina vazia: %s | pagina=%s",
                        self.pdf, i,
                    )
                    return "", total_paginas

                text += page_text + "\n"
                logger.debug(
                    "Pagina extraida: arquivo=%s | pagina=%s | caracteres=%s | tempo=%.2fs",
                    self.pdf, i, len(page_text), time.perf_counter() - inicio_pagina,
                )

        caracteres = len(text or "")
        tempo_total = time.perf_counter() - inicio
        logger.info(
            "Extracao concluida: %s | caracteres=%s | paginas=%s | tempo=%.2fs",
            self.pdf, caracteres, total_paginas, tempo_total,
        )

        if caracteres == 0:
            logger.warning(
                "PDF sem texto extraivel ou conteudo insuficiente: %s | caracteres=0",
                self.pdf,
            )

        return text.split("\n"), total_paginas
