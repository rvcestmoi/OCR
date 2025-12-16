import os
import json


def build_supplier_key(iban, bic):
    if not iban or not bic:
        return None
    return f"{iban}_{bic}"

def load_supplier_model(supplier_key):
    model_dir = r"C:\git\OCR\OCR\models\suppliers"
    path = os.path.join(model_dir, f"{supplier_key}.json")

    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)