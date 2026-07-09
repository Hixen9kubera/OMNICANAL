"""
Mapeo de guías de tallas (SIZE_GRID_ID) por cuenta + dominio + género.
Cuando publisher.py publica un item de calzado/ropa, busca aquí el chart_id
correspondiente y lo inyecta como atributo SIZE_GRID_ID en el payload.

Para agregar nuevas guías:
1. Crearlas en ML (vía API POST /catalog/charts o desde dashboard)
2. Agregar el chart_id en el dict de abajo con key "DOMAIN:GENDER"
"""

# Catálogo de guías por cuenta. Key: "DOMAIN:GENDER" -> chart_id (string)
# Gender debe coincidir con el value_name del atributo GENDER en ML
# (ej: "Hombre", "Mujer", "Niñas", "Niños", "Sin género", "Sin género infantil")
CHARTS_BY_ACCOUNT = {
    'BEKURA': {
        'SANDALS_AND_CLOGS:Hombre':    '5601946',
        'SNEAKERS:Hombre':             '5601948',
        'SNEAKERS:Mujer':              '5602224',
        'BOOTS_AND_BOOTIES:Hombre':    '5602034',
        'LOAFERS_AND_OXFORDS:Hombre':  '5601950',
        # Brasieres ya creado en BEKURA
        'BRAS:Mujer':                  '5269931',
    },
    'SANCORFASHION': {
        'SANDALS_AND_CLOGS:Hombre':    '6009679',
        'SNEAKERS:Hombre':             '4538718',  # 4550104 tambien existe, 4538718 es la mas nueva
        'SNEAKERS:Mujer':              '4821199',
        'SNEAKERS:Sin género':         '4827537',
        'BOOTS_AND_BOOTIES:Hombre':    '5601952',
        'BOOTS_AND_BOOTIES:Sin género infantil': '4572778',
        'LOAFERS_AND_OXFORDS:Hombre':  '5601954',
        'SAFETY_FOOTWEAR:Hombre':      '4859025',
        'BRAS:Mujer':                  '4922945',
    },
}


def get_chart_id(cuenta: str, domain: str, gender: str) -> str | None:
    """
    Devuelve el chart_id (SIZE_GRID_ID) para la combinación cuenta+dominio+género,
    o None si no hay guía configurada (publisher omite el atributo en ese caso).
    """
    if not cuenta or not domain or not gender:
        return None
    key = f'{domain}:{gender}'
    return CHARTS_BY_ACCOUNT.get(cuenta, {}).get(key)
