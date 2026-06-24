import logging
import time
from pathlib import Path

import pdfplumber

from src.utils.helpers import log_memoria

logger = logging.getLogger(__name__)


class PDFExtractor:
    def __init__(self, pdf):
        self.pdf = pdf

    def extract(self) -> tuple[list[str], int]:
        pdf_path = Path(self.pdf)
        inicio_total = time.perf_counter()

        logger.info(
            "Iniciando extracao do PDF | arquivo=%s | existe=%s | tamanho_bytes=%s",
            pdf_path.name,
            pdf_path.exists(),
            pdf_path.stat().st_size if pdf_path.exists() else None,
        )
        log_memoria(f"antes_abrir_pdf_{pdf_path.name}")

        if not pdf_path.exists():
            logger.error("Arquivo PDF nao encontrado: %s", self.pdf)
            return "", 0

        if pdf_path.stat().st_size == 0:
            logger.warning("Arquivo PDF vazio (0 bytes): %s", pdf_path.name)
            return "", 0

        text = ""
        total_paginas = 0

        inicio_abertura = time.perf_counter()
        logger.info("Abrindo PDF com pdfplumber: %s", pdf_path.name)

        try:
            pdf = pdfplumber.open(self.pdf)
        except Exception:
            logger.exception("Erro ao abrir PDF: %s", pdf_path.name)
            return "", 0

        tempo_abertura = time.perf_counter() - inicio_abertura
        logger.info(
            "PDF aberto com sucesso | arquivo=%s | tempo=%.2fs",
            pdf_path.name, tempo_abertura,
        )
        log_memoria(f"depois_abrir_pdf_{pdf_path.name}")

        try:
            total_paginas = len(pdf.pages)
            logger.info(
                "Paginas detectadas | arquivo=%s | paginas=%s",
                pdf_path.name, total_paginas,
            )

            if total_paginas == 0:
                logger.warning(
                    "PDF sem paginas: %s | paginas=0",
                    pdf_path.name,
                )
                return "", 0

            for i, page in enumerate(pdf.pages, start=1):
                inicio_pagina = time.perf_counter()
                log_memoria(f"antes_extrair_pagina_{i}_{pdf_path.name}")

                logger.info(
                    "Iniciando extracao da pagina %s/%s | arquivo=%s",
                    i, total_paginas, pdf_path.name,
                )

                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    logger.exception(
                        "Erro ao extrair pagina %s/%s | arquivo=%s",
                        i, total_paginas, pdf_path.name,
                    )
                    raise

                tempo_pagina = time.perf_counter() - inicio_pagina

                if tempo_pagina > 10:
                    logger.warning(
                        "Extracao lenta detectada | arquivo=%s | pagina=%s/%s | tempo=%.2fs",
                        pdf_path.name, i, total_paginas, tempo_pagina,
                    )

                logger.info(
                    "Pagina extraida com sucesso %s/%s | caracteres=%s | tempo=%.2fs",
                    i, total_paginas, len(page_text), tempo_pagina,
                )
                log_memoria(f"depois_extrair_pagina_{i}_{pdf_path.name}")

                if "cid:" in page_text:
                    logger.warning(
                        "PDF com texto nao extraivel (cid): %s | pagina=%s/%s",
                        pdf_path.name, i, total_paginas,
                    )
                    return "", total_paginas

                if not page_text.strip():
                    logger.warning(
                        "PDF com pagina vazia: %s | pagina=%s/%s",
                        pdf_path.name, i, total_paginas,
                    )
                    return "", total_paginas

                text += page_text + "\n"

        finally:
            pdf.close()

        caracteres = len(text or "")
        tempo_total = time.perf_counter() - inicio_total

        logger.info(
            "Extracao concluida | arquivo=%s | paginas=%s | caracteres=%s | tempo=%.2fs",
            pdf_path.name, total_paginas, caracteres, tempo_total,
        )
        log_memoria(f"depois_texto_final_{pdf_path.name}")

        if caracteres == 0:
            logger.warning(
                "PDF sem texto extraivel: %s | caracteres=0",
                pdf_path.name,
            )

        return text.split("\n"), total_paginas
