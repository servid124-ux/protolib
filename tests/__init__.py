"""
tests/

Suite de pruebas de protolib. Corre sin dependencias extra (solo la
librería estándar + pyyaml, que ya es parte de los extras del propio
paquete) usando `unittest`, así que también funciona con pytest si lo
tenés instalado (pytest ejecuta clases unittest.TestCase de forma
nativa) sin necesitar nada adicional.

Uso:
    python -m unittest discover -s tests -v
    # o, si tenés pytest instalado:
    pytest tests/

Este __init__ agrega la raíz del repo a sys.path ANTES de que se
importe cualquier test_*.py, usando la ubicación de este archivo (no
el cwd del proceso) -- el mismo patrón que el propio README recomienda
para los scripts de examples/, para que la suite corra igual sin
importar desde qué directorio se invoque.
"""
import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

EXAMPLES_DIR = os.path.join(_repo_root, "examples")
