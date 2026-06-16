import os
import gc
import shutil
import pandas as pd
from copy import copy
from pathlib import Path
from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter

def planilha_lancamento(df, destino):
    destino = Path(destino)
    destino_tmp = destino.with_name(f"{destino.stem}_tmp{destino.suffix}")

    shutil.copy2(destino, destino_tmp)

    wb = None

    try:
        wb = load_workbook(destino_tmp, keep_vba=True)
        ws = wb["Plan1"]

        linha_inicial = 2

        for posicao, (_, row) in enumerate(df.iterrows()):
            linha_excel = linha_inicial + posicao

            # DATA -> coluna A
            celula_data = ws[f"A{linha_excel}"]
            celula_data.number_format = "@"
            celula_data.value = str(row["DATA"])

            # VALOR -> coluna D
            celula_valor = ws[f"D{linha_excel}"]
            celula_valor.value = abs(row["VALOR"])

            fonte = copy(celula_valor.font)

            if row["TIPO"] == "D":
                fonte.color = "FF0000"
            else:
                fonte.color = "000000"

            celula_valor.font = fonte

            # DESCRIÇÃO -> coluna F
            ws[f"F{linha_excel}"] = row["DESCRIÇÃO"]

        wb.save(destino_tmp)

    finally:
        if wb is not None:
            # Fecha o arquivo interno usado para preservar macros
            if hasattr(wb, "vba_archive") and wb.vba_archive is not None:
                try:
                    wb.vba_archive.close()
                except Exception:
                    pass

                try:
                    wb.vba_archive = None
                except Exception:
                    pass

            try:
                wb.close()
            except Exception:
                pass

            del wb
            gc.collect()

    os.replace(destino_tmp, destino)

    print(f"Arquivo preenchido com sucesso: {destino}")

def totalizador(df):
    df = df.reset_index(drop=True)
    saida = df[df["TIPO"] == "D"]["VALOR"].sum()
    entrada = df[df["TIPO"] == "C"]["VALOR"].sum()
    df.loc[len(df)] = {
        "DATA": "",
        "DESCRIÇÃO": "TOTAL ENTRADAS",
        "VALOR": entrada,
        "TIPO": "C"
    }

    df.loc[len(df)] = {
        "DATA": "",
        "DESCRIÇÃO": "TOTAL SAÍDAS",
        "VALOR": saida,
        "TIPO": "D"
    }
    return df

def remover_senha_pdf(caminho_pdf: str, senha: str, caminho_saida: str | None = None):
    entrada = Path(caminho_pdf)

    if caminho_saida is None:
        caminho_saida = entrada.with_name(f"{entrada.stem}_sem_senha.pdf")

    reader = PdfReader(str(entrada))

    if reader.is_encrypted:
        resultado = reader.decrypt(senha)

        if resultado == 0:
            raise ValueError("Senha inválida ou não foi possível descriptografar o PDF.")

    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    with open(caminho_saida, "wb") as arquivo_saida:
        writer.write(arquivo_saida)

    return caminho_saida