from abc import ABC
import pandas as pd


def layout(*signatures: str):
    """Marca um método como layout candidato.

    Todas as `signatures` precisam aparecer nas primeiras linhas do PDF
    para o layout ser selecionado.
    """
    def decorator(func):
        func._layout_signatures = signatures
        return func
    return decorator


class BankHandler(ABC):
    bank: str
    head_lines: int = 15

    def _layouts(self):
        return [
            getattr(self, name)
            for name in vars(type(self))
            if hasattr(getattr(self, name, None), "_layout_signatures")
        ]

    def _pick_layout(self, pdf: list[str]):
        head = "\n".join(pdf[: self.head_lines])
        candidates = [
            fn
            for fn in self._layouts()
            if all(sig in head for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))

    def matches(self, pdf: list[str]) -> bool:
        return self._pick_layout(pdf) is not None

    def parse(self, pdf: list[str]) -> pd.DataFrame:
        fn = self._pick_layout(pdf)
        if fn is None:
            raise ValueError(f"{self.bank}: nenhum layout reconhecido")
        return fn(pdf)
