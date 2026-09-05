"""
Dataclasses pour les messages de métiers Dofus Rétro 1.29.

Messages couverts (serveur privé) :
  JX  — JobXP  (S→C) : stats complètes de tous les métiers d'un personnage
         Format : K{charId}~{jobId};{level};{xp};{xpNext};{xpTotal};|{jobId2};...

  JO  — JobChangeJobStats (S→C) : présence d'un métier (niveau toujours 0 sur ce serveur)
         Format : {jobId}|0|0  → ignoré, les vraies stats viennent de JX
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Noms des métiers Dofus Rétro 1.29
# IDs authentiques depuis Ancestra-Remake (émulateur 1.29.1 de référence) :
# https://github.com/Done52/Ancestra_Remake (Constants.java)
JOB_NAMES: dict[int, str] = {
    # Récolte
    2:  "Bûcheron",
    24: "Mineur",
    26: "Alchimiste",
    28: "Paysan",
    36: "Pêcheur",
    41: "Chasseur",
    # Artisanat - Forgeurs (armes en métal)
    11: "Forgeur d'épées",
    14: "Forgeur de marteaux",
    17: "Forgeur de dagues",
    20: "Forgeur de pelles",
    31: "Forgeur de haches",
    60: "Forgeur de boucliers",
    # Artisanat - Sculpteurs (armes en bois)
    13: "Sculpteur d'arcs",
    18: "Sculpteur de bâtons",
    19: "Sculpteur de baguettes",
    # Artisanat - Tissu, cuir, bijoux
    15: "Cordonnier",
    16: "Bijoutier",
    25: "Boulanger",
    27: "Tailleur",
    56: "Boucher",
    58: "Poissonnier",
    65: "Bricoleur",
    # Forgemagie (spécialisations niveau 65+)
    43: "Forgemage de dagues",
    44: "Forgemage d'épées",
    45: "Forgemage de marteaux",
    46: "Forgemage de pelles",
    47: "Forgemage de haches",
    48: "Sculptemage d'arcs",
    49: "Sculptemage de baguettes",
    50: "Sculptemage de bâtons",
    62: "Cordomage",
    63: "Joaillomage",
    64: "Costumage",
}


@dataclass
class JobStats:
    """Stats d'un métier."""

    job_id: int = 0
    level: int = 1
    xp: int = 0
    xp_next: int = 0
    xp_total: int = 0

    @property
    def name(self) -> str:
        return JOB_NAMES.get(self.job_id, f"Métier#{self.job_id}")


def parse_jx(payload: str, my_char_id: str) -> list[JobStats]:
    """Parser le message JX — stats complètes de tous les métiers.

    Format : K{charId}~{jobId};{level};{xp};{xpNext};{xpTotal};|{jobId2};...

    Args:
        payload: Tout ce qui suit 'JX'.
        my_char_id: ID de notre personnage principal (str ou int).
                    Seuls les JX pour ce perso sont traités.

    Returns:
        Liste de JobStats, ou [] si le message ne concerne pas notre perso.
    """
    if not payload.startswith("K"):
        return []

    # K{charId}~{données}
    tilde_pos = payload.find("~")
    if tilde_pos < 0:
        return []

    char_id = payload[1:tilde_pos].strip()
    if char_id != str(my_char_id):
        return []   # Pas notre perso

    jobs_part = payload[tilde_pos + 1:]
    results: list[JobStats] = []

    def _int(s: str) -> int:
        s = s.strip().rstrip(";")
        return int(s) if s.lstrip("-").isdigit() else 0

    for chunk in jobs_part.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(";")
        if not parts[0]:
            continue
        job = JobStats()
        try:
            job.job_id  = _int(parts[0]) if len(parts) > 0 else 0
            job.level   = _int(parts[1]) if len(parts) > 1 else 1
            job.xp      = _int(parts[2]) if len(parts) > 2 else 0
            job.xp_next = _int(parts[3]) if len(parts) > 3 else 0
            job.xp_total= _int(parts[4]) if len(parts) > 4 else 0
            # Filtrer les IDs inconnus : le serveur Abrak envoie ~39 slots
            # dont certains pseudo-jobs (1, 22, 42, 110, 121, 151) qui ne
            # correspondent à aucun métier réel de Dofus 1.29.
            if job.job_id in JOB_NAMES:
                results.append(job)
        except Exception as exc:
            logger.debug("[Jobs] chunk JX ignoré %r : %s", chunk[:40], exc)

    return results
