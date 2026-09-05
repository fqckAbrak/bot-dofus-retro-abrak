"""No-anim — déplacements instantanés via swap du `core.swf` du client.

Le client Abrak Rétro est le moteur Dofus 1.29 (ActionScript 2) embarqué dans
Electron/PepperFlash. Tout le code de jeu vit dans
``…/retroclient/modules/core.swf``.

On fournit une variante patchée (``core.noanim.swf``) dans laquelle l'animation
de glissement des entités — la fonction ``basicMove`` de la classe sprite du
champ de bataille (``ank.battlefield.mc[...]``) — **téléporte** le sprite à sa
case de destination en une seule frame **uniquement pendant un combat actif**
(``this.api.datacenter.Game.isRunning`` — ``api`` est l'alias de ``_global.API``
posé par le constructeur de la classe ; ``isRunning`` est posé à ``true`` au
*GameStartToPlay* et remis à ``false`` en fin de combat). Hors combat — y
compris la phase d'agression/approche — le déplacement reste normal (glissement
d'origine intact) : on ne touche à rien sur la carte.

C'est sûr du point de vue de l'état de jeu : ``moveToCell`` met déjà à jour la
cellule, ``isInMove`` et la carte **synchroniquement** avant de lancer
l'animation ; ``basicMove`` n'est que le rendu visuel. Le serveur 1.29 attend
l'ack client (``GKK``) de chaque action avant d'enchaîner, donc rendre le
glissement instantané en combat accélère aussi bien les déplacements des
monstres que ceux des joueurs — exactement ce qu'on cherche, sans rien changer
hors combat.

Stratégie de bascule (phase A) : on copie la variante voulue par-dessus
``core.swf``. La modification prend effet **au prochain lancement** du client
(le SWF est chargé une fois au démarrage). Trois fichiers cohabitent dans le
dossier ``modules`` :

- ``core.swf``          : le fichier réellement chargé par le client.
- ``core.swf.orig.bak`` : copie vierge d'origine (no-anim OFF).
- ``core.noanim.swf``   : variante patchée (no-anim ON).

L'état actif est déterminé en comparant le hash de ``core.swf`` à celui de
``core.noanim.swf`` — pas besoin de persister un flag séparé, la vérité est sur
le disque.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

CORE_NAME = "core.swf"
ORIG_BAK_NAME = "core.swf.orig.bak"
NOANIM_NAME = "core.noanim.swf"

# Sous-chemin du dossier modules relatif au `game_path` configuré, pour les
# installs Abrak standard. On tente aussi une recherche récursive en repli.
_KNOWN_SUBPATHS = (
    os.path.join("Abrak", "Retro", "resources", "app", "retroclient", "modules"),
    os.path.join("Retro", "resources", "app", "retroclient", "modules"),
    os.path.join("resources", "app", "retroclient", "modules"),
    os.path.join("retroclient", "modules"),
    "modules",
)


@dataclass
class NoAnimStatus:
    """État du no-anim pour une install donnée."""

    modules_dir: str | None
    enabled: bool
    variant_available: bool
    backup_available: bool
    reason: str = ""

    @property
    def installable(self) -> bool:
        """True si on peut basculer le no-anim (dossier + variante présents)."""
        return bool(self.modules_dir) and self.variant_available


def _sha1(path: str) -> str | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def find_modules_dir(game_path: str | None) -> str | None:
    """Localise le dossier ``…/retroclient/modules`` contenant ``core.swf``.

    Essaie d'abord les sous-chemins connus relatifs à ``game_path``, puis tombe
    sur une recherche récursive bornée.
    """
    if not game_path:
        return None
    game_path = os.path.normpath(game_path)
    if not os.path.isdir(game_path):
        return None

    for sub in _KNOWN_SUBPATHS:
        cand = os.path.join(game_path, sub)
        if os.path.isfile(os.path.join(cand, CORE_NAME)):
            return os.path.normpath(cand)

    # Repli : recherche récursive d'un `retroclient/modules/core.swf`.
    for root, _dirs, files in os.walk(game_path):
        if (
            CORE_NAME in files
            and os.path.basename(root) == "modules"
            and os.path.basename(os.path.dirname(root)) == "retroclient"
        ):
            return os.path.normpath(root)
    return None


def status(game_path: str | None) -> NoAnimStatus:
    """Renvoie l'état courant du no-anim pour l'install pointée par game_path."""
    modules = find_modules_dir(game_path)
    if not modules:
        return NoAnimStatus(
            modules_dir=None, enabled=False, variant_available=False,
            backup_available=False,
            reason="Dossier modules/core.swf introuvable sous le chemin du jeu.",
        )

    core = os.path.join(modules, CORE_NAME)
    noanim = os.path.join(modules, NOANIM_NAME)
    bak = os.path.join(modules, ORIG_BAK_NAME)

    variant_available = os.path.isfile(noanim)
    backup_available = os.path.isfile(bak)

    enabled = False
    if variant_available:
        core_hash = _sha1(core)
        enabled = core_hash is not None and core_hash == _sha1(noanim)

    reason = ""
    if not variant_available:
        reason = (
            "Variante patchée core.noanim.swf absente — génère-la avec "
            "tools/build_noanim_swf.py."
        )
    return NoAnimStatus(
        modules_dir=modules, enabled=enabled,
        variant_available=variant_available, backup_available=backup_available,
        reason=reason,
    )


def is_abrak_running() -> bool:
    """True si au moins un client Abrak.exe tourne (le SWF serait verrouillé)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Abrak.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "Abrak.exe" in out
    except Exception:
        return False


def set_enabled(game_path: str | None, enabled: bool) -> tuple[bool, str]:
    """Active/désactive le no-anim en échangeant le fichier core.swf.

    Renvoie ``(ok, message)``. La bascule prend effet au prochain lancement du
    client ; si un client tourne, le message le signale (et l'écriture peut
    échouer si le fichier est verrouillé).
    """
    st = status(game_path)
    if not st.modules_dir:
        return False, st.reason or "Dossier du client introuvable."

    core = os.path.join(st.modules_dir, CORE_NAME)
    noanim = os.path.join(st.modules_dir, NOANIM_NAME)
    bak = os.path.join(st.modules_dir, ORIG_BAK_NAME)

    if enabled:
        if not st.variant_available:
            return False, (
                "core.noanim.swf manquant. Génère-le avec "
                "tools/build_noanim_swf.py puis réessaie."
            )
        # S'assurer qu'une sauvegarde vierge existe avant d'écraser core.swf.
        if not os.path.isfile(bak):
            core_hash = _sha1(core)
            if core_hash == _sha1(noanim):
                return False, (
                    "core.swf est déjà la version no-anim mais aucune "
                    "sauvegarde d'origine n'existe. Restaure un core.swf vierge."
                )
            try:
                shutil.copy2(core, bak)
            except OSError as exc:
                return False, f"Impossible de sauvegarder l'original : {exc}"
        src, dst = noanim, core
    else:
        if not os.path.isfile(bak):
            return False, "Aucune sauvegarde core.swf.orig.bak à restaurer."
        src, dst = bak, core

    try:
        shutil.copy2(src, dst)
    except PermissionError:
        return False, (
            "core.swf est verrouillé : ferme tous les clients Abrak puis "
            "réessaie."
        )
    except OSError as exc:
        return False, f"Échec de la copie : {exc}"

    running = is_abrak_running()
    suffix = (
        " Un client Abrak est ouvert : relance-le pour appliquer."
        if running else " Prendra effet au prochain lancement du client."
    )
    return True, ("No-anim activé." if enabled else "No-anim désactivé.") + suffix


# ---------------------------------------------------------------------------
# Génération de la variante patchée (core.noanim.swf) via FFDec (JPEXS)
# ---------------------------------------------------------------------------
# Patch au niveau P-code (pas de recompilation source) : cf. docstring de
# tools/build_noanim_swf.py pour le détail de la méthode et son justificatif.
# Regroupé ici (plutôt que dans tools/) pour être appelable depuis le
# dashboard (bouton "Patcher no-anim" de l'onglet Paramètres) comme en CLI.

_LABEL = "locNoAnimSkip"
_INJECT_PCODE = [
    'Push register1, "api"',
    "GetMember",
    'Push "datacenter"',
    "GetMember",
    'Push "Game"',
    "GetMember",
    'Push "isRunning"',
    "GetMember",
    "Not",
    f"If {_LABEL}",
    'Push register1, "_x", register1, "_x"',
    "GetMember",
    'Push register1, "_nDistance"',
    "GetMember",
    "Push register7",
    "Multiply",
    "Add2",
    "SetMember",
    'Push register1, "_y", register1, "_y"',
    "GetMember",
    'Push register1, "_nDistance"',
    "GetMember",
    "Push register6",
    "Multiply",
    "Add2",
    "SetMember",
    'Push register1, "_nDistance", 0.0',
    "SetMember",
    'Push register1, "_nLastTimer"',
    "GetTime",
    "SetMember",
    'Push register1, "_nStartCount", 0.0',
    "SetMember",
    "Push false",
    "Return",
]


def _find_ffdec(explicit: str | None) -> str | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("FFDEC_JAR"):
        candidates.append(os.environ["FFDEC_JAR"])
    candidates += [
        r"E:\tools\jpexs\ffdec-cli.jar",
        r"E:\tools\jpexs\ffdec.jar",
        r"C:\Program Files\FFDec\ffdec-cli.jar",
        r"C:\Program Files (x86)\FFDec\ffdec-cli.jar",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _run(ffdec: str, *args: str) -> None:
    cmd = ["java", "-jar", ffdec, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Java introuvable (nécessaire pour exécuter FFDec). "
            "Installe un JRE (ex: Temurin/OpenJDK) et réessaie."
        ) from exc
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        raise RuntimeError(f"FFDec a échoué (code {proc.returncode}) : {detail[-800:]}")


def _patch_basicmove_pcode(text: str) -> str | None:
    """Insère le bloc de téléportation en tête de la fonction ``basicMove``."""
    lines = text.split("\n")
    anchor = None
    for i, ln in enumerate(lines[:-1]):
        if ln.strip() == 'Push "basicMove"' and lines[i + 1].lstrip().startswith(
            "DefineFunction2"
        ):
            anchor = i + 1
            break
    if anchor is None:
        return None

    body0 = anchor + 1
    first = lines[body0]
    indent = first[: len(first) - len(first.lstrip())]

    injected = [indent + s for s in _INJECT_PCODE]
    labeled = indent + _LABEL + ":" + first.lstrip()
    new_lines = lines[:body0] + injected + [labeled] + lines[body0 + 1:]
    return "\n".join(new_lines)


def _patch_launch_visual_effect_pcode(text: str) -> str | None:
    """Saute l'animation du lanceur (``setAnim``) en combat actif."""
    if '"launchVisualEffect"' not in text or '"setAnim"' not in text:
        return None
    if '"SkipFightPlayerAnimations"' not in text:
        return None

    lines = text.split("\n")

    inj_at = label = None
    for i in range(3, len(lines)):
        s = lines[i].lstrip()
        if not s.startswith("If "):
            continue
        if lines[i - 1].strip() != "Not" or lines[i - 2].strip() != "Not":
            continue
        if not re.match(r"Push register\d+$", lines[i - 3].strip()):
            continue
        lbl = s.split(None, 1)[1].strip()
        body, j, found = [], i + 1, False
        while j < len(lines) and j < i + 40:
            if lines[j].lstrip().startswith(lbl + ":"):
                found = True
                break
            body.append(lines[j])
            j += 1
        if found and any('"setAnim"' in b for b in body):
            inj_at, label = i - 3, lbl
            break

    if inj_at is None:
        return None

    if lines and lines[0].startswith("ConstantPool") and '"isRunning"' not in lines[0]:
        lines[0] = lines[0] + ', "isRunning"'

    indent = lines[inj_at][: len(lines[inj_at]) - len(lines[inj_at].lstrip())]
    inject = [
        'Push "_global"',
        "GetVariable",
        'Push "API"',
        "GetMember",
        'Push "datacenter"',
        "GetMember",
        'Push "Game"',
        "GetMember",
        'Push "isRunning"',
        "GetMember",
        f"If {label}",
    ]
    new_lines = lines[:inj_at] + [indent + s for s in inject] + lines[inj_at:]
    return "\n".join(new_lines)


def _patch_worldmap_doubleclick_pcode(text: str) -> str | None:
    """Déverrouille le double-clic « voyage » sur la carte du monde (MapExplorer)."""
    if '"isAuthorized"' not in text or '"doubleClick"' not in text:
        return None
    lines = text.split("\n")
    for i in range(len(lines) - 3):
        if lines[i].strip() != 'Push "isAuthorized"':
            continue
        if lines[i + 1].strip() != "GetMember" or lines[i + 2].strip() != "Not":
            continue
        s_if = lines[i + 3].strip()
        if not s_if.startswith("If "):
            continue
        label = s_if.split(None, 1)[1].strip()
        block, j, found_label = [], i + 4, False
        while j < len(lines) and j < i + 4 + 30:
            if lines[j].lstrip().startswith(label + ":"):
                found_label = True
                break
            block.append(lines[j])
            j += 1
        blob = "\n".join(block)
        if not (found_label and '"doubleClick"' in blob and '"addEventListener"' in blob):
            continue
        indent = lines[i + 2][: len(lines[i + 2]) - len(lines[i + 2].lstrip())]
        return "\n".join(lines[:i + 2] + [indent + "Pop"] + lines[i + 4:])
    return None


# Patches appliqués au P-code. Le 1er (déplacement) est **critique** ; les autres
# sont des bonus — s'ils sont introuvables après une MAJ du client, on continue
# quand même avec le no-anim déplacement.
_PATCHERS = (
    ("déplacement instantané (basicMove)", _patch_basicmove_pcode, True),
    ("anim lanceur en combat (launchVisualEffect)", _patch_launch_visual_effect_pcode, False),
    ("déverrouillage double-clic carte du monde (autopilote)", _patch_worldmap_doubleclick_pcode, False),
)


def generate_variant(
    game_path: str | None,
    ffdec_path: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Génère ``core.noanim.swf`` à partir du ``core.swf`` du ``game_path`` donné.

    Patch au niveau P-code via FFDec (JPEXS) — voir le module docstring et
    ``tools/build_noanim_swf.py`` pour le détail. Appelable depuis le
    dashboard (bouton) ou en CLI. ``progress``, si fourni, reçoit un message
    à chaque étape (pour affichage live dans l'UI).

    Renvoie ``(ok, message)``.
    """
    def _step(msg: str) -> None:
        logger.info("[noanim] %s", msg)
        if progress:
            progress(msg)

    modules = find_modules_dir(game_path)
    if not modules:
        return False, f"Dossier modules/core.swf introuvable sous : {game_path!r}"
    _step(f"Dossier modules : {modules}")

    core = os.path.join(modules, CORE_NAME)
    bak = os.path.join(modules, ORIG_BAK_NAME)
    out = os.path.join(modules, NOANIM_NAME)

    ffdec = _find_ffdec(ffdec_path)
    if not ffdec:
        return False, (
            "FFDec (JPEXS) introuvable. Installe-le depuis "
            "https://github.com/jindrapetrik/jpexs-decompiler/releases, puis "
            "renseigne le chemin de ffdec-cli.jar ci-dessus, ou définis la "
            "variable d'environnement FFDEC_JAR."
        )
    _step(f"FFDec : {ffdec}")

    if is_abrak_running():
        return False, (
            "Un client Abrak est ouvert — ferme-le avant de générer la "
            "variante (le core.swf source doit être lisible sans verrou)."
        )

    if os.path.isfile(bak):
        source = bak
    else:
        source = core
        try:
            shutil.copy2(core, bak)
        except OSError as exc:
            return False, f"Impossible de sauvegarder l'original : {exc}"
        _step(f"Sauvegarde créée : {bak}")
    _step(f"Source : {source}")

    try:
        with tempfile.TemporaryDirectory(prefix="noanim_") as tmp:
            export_dir = os.path.join(tmp, "export")
            _step("Export du P-code AS2…")
            _run(ffdec, "-format", "script:pcode", "-export", "script", export_dir, source)

            patch_root = os.path.join(tmp, "patch")
            applied: set[str] = set()
            for root, _dirs, files in os.walk(export_dir):
                for name in files:
                    if not name.endswith(".pcode"):
                        continue
                    p = os.path.join(root, name)
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            txt = f.read()
                    except OSError:
                        continue
                    current, changed = txt, False
                    for label, fn, _critical in _PATCHERS:
                        if label in applied:
                            continue
                        res = fn(current)
                        if res is not None:
                            current, changed = res, True
                            applied.add(label)
                            _step(f"Patch : {label} → {os.path.relpath(p, export_dir)}")
                    if changed:
                        dst = os.path.join(patch_root, os.path.relpath(p, export_dir))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(dst, "w", encoding="utf-8") as f:
                            f.write(current)

            missing_critical = [
                label for label, _fn, critical in _PATCHERS
                if critical and label not in applied
            ]
            if missing_critical:
                return False, (
                    "Patch critique introuvable dans le P-code (" +
                    ", ".join(missing_critical) + ") — la structure du client a "
                    "peut-être changé (mise à jour Abrak ?). À revoir manuellement."
                )
            warnings = [
                label for label, _fn, critical in _PATCHERS
                if not critical and label not in applied
            ]
            for w in warnings:
                _step(f"⚠ Patch optionnel non appliqué (motif introuvable) : {w}")

            _step("Réassemblage du P-code patché…")
            _run(ffdec, "-importScript", source, out, patch_root)
    except RuntimeError as exc:
        return False, str(exc)

    msg = f"Variante no-anim générée : {out}"
    if warnings:
        msg += f" ({len(warnings)} patch(s) optionnel(s) non appliqué(s))"
    return True, msg
