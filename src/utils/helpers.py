import os
import logging
import shutil
import pandas as pd
from copy import copy
from pathlib import Path
from openpyxl import load_workbook
from datetime import datetime, date
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


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
            tipo = str(row["TIPO"]).upper().strip()

            if tipo == "D":
                fonte.color = "FFFF0000"  # vermelho
            else:
                fonte.color = "FF000000"  # preto

            celula_valor.font = fonte

            # DESCRIÇÃO -> coluna F
            ws[f"F{linha_excel}"] = row["DESCRIÇÃO"]

        wb.save(destino_tmp)

    finally:
        if wb is not None:
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

def normalizar_parc(valor):
    if pd.isna(valor):
        return ""

    # Quando o Excel transformou 01/02 em data
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return f"{valor.day:02}/{valor.month:02}"

    # Quando vem número 1.0, 2.0 etc.
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))

    return str(valor).strip()

def corrigir_xls_html_para_xlsx(
    caminho_xls: str | Path,
    caminho_xlsx: str | Path | None = None,
    deletar_original: bool = True
) -> Path:
    """
    Converte um arquivo .xls/.html que na verdade é HTML para .xlsx válido.

    Se deletar_original=True, remove o arquivo original somente depois
    que o .xlsx for criado com sucesso.

    Retorna:
        Path do arquivo .xlsx gerado.
    """

    caminho_xls = Path(caminho_xls)

    if not caminho_xls.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_xls}")

    if caminho_xlsx is None:
        caminho_xlsx = caminho_xls.with_suffix(".xlsx")
    else:
        caminho_xlsx = Path(caminho_xlsx)

        # Se passou apenas o nome do arquivo, salva na mesma pasta do .xls
        if not caminho_xlsx.is_absolute():
            caminho_xlsx = caminho_xls.parent / caminho_xlsx

    # Lê tabelas HTML dentro do arquivo .xls
    tabelas = pd.read_html(caminho_xls)

    if not tabelas:
        raise ValueError(f"Nenhuma tabela HTML encontrada em: {caminho_xls}")

    df = tabelas[0]

    # Remove linhas e colunas totalmente vazias
    df = df.dropna(how="all").dropna(axis=1, how="all")

    # Garante que a pasta de destino exista
    caminho_xlsx.parent.mkdir(parents=True, exist_ok=True)

    # Salva como .xlsx real
    with pd.ExcelWriter(caminho_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Planilha1")

    # Só apaga o original se o .xlsx realmente foi criado
    if deletar_original and caminho_xlsx.exists() and caminho_xlsx.stat().st_size > 0:
        caminho_xls.unlink()

    return caminho_xlsx

