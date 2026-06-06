import pandas as pd
from pathlib import Path
import win32com.client as win32
from pypdf import PdfReader, PdfWriter

def planilha_lancamento(df, destino):
    excel = win32.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False

    try:
        wb = excel.Workbooks.Open(str(destino))
        ws = wb.Worksheets("Plan1")

        linha_inicial = 2

        for posicao, (_, row) in enumerate(df.iterrows()):
            linha_excel = linha_inicial + posicao

            # DATA -> coluna A
            celula_data = ws.Range(f"A{linha_excel}")
            celula_data.NumberFormat = "@"
            celula_data.Value = row["DATA"]

            # VALOR -> coluna D
            valor = abs(row["VALOR"])

            celula_valor = ws.Range(f"D{linha_excel}")
            celula_valor.Value = valor

            if row["TIPO"] == "D":
                celula_valor.Font.Color = 255  # vermelho
            else:
                celula_valor.Font.Color = 0    # preto

            # DESCRIÇÃO -> coluna F
            ws.Range(f"F{linha_excel}").Value = row["DESCRIÇÃO"]

        wb.Save()
        wb.Close(SaveChanges=True)

    finally:
        excel.Quit()

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