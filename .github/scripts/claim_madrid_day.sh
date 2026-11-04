#!/usr/bin/env bash
# Reserva la fecha de hoy en Europe/Madrid para que un workflow haga su
# trabajo real como mucho una vez al día.
#
# GitHub dispara dos cron (CET y CEST) y además puede arrancar cada uno
# varias horas tarde. El primero que hace push del marcador se queda el
# día; el otro no llega a llamar a la API. Un workflow_dispatch ignora el
# marcador y siempre sigue (run=true), para poder reintentar a mano.
#
#   claim_madrid_day.sh peek <marcador-relativo>
#   claim_madrid_day.sh <marcador-relativo> <asunto-del-commit>
#   claim_madrid_day.sh push
#
# peek solo mira el marcador (no hace push): run=false si hoy ya está
# reservado y el evento no es workflow_dispatch. Así se puede saltar
# pip/setup antes de reservar. La reserva de verdad va justo antes de
# llamar a la API: si pip falla, el otro cron todavía puede quedarse el día.
#
# El modo push reintenta el push de HEAD y, si la rama avanzó (el otro
# workflow también commitea), rebasa encima. No escribe GITHUB_OUTPUT.
set -euo pipefail

export GIT_TERMINAL_PROMPT=0

cd_workspace() {
  if [ -n "${GITHUB_WORKSPACE:-}" ]; then
    cd "$GITHUB_WORKSPACE"
  fi
}

require_branch() {
  if [ -z "${GITHUB_REF_NAME:-}" ]; then
    echo "Falta GITHUB_REF_NAME." >&2
    exit 1
  fi
  branch="$GITHUB_REF_NAME"
}

push_head() {
  local attempt
  cd_workspace
  require_branch
  for attempt in 1 2 3 4; do
    if git push origin "HEAD:${branch}"; then
      return 0
    fi
    echo "Push rechazado (intento ${attempt}); se rebasa sobre origin/${branch}."
    git fetch origin "$branch"
    git rebase "origin/${branch}"
    sleep "$attempt"
  done
  echo "No se pudo hacer push a ${branch}." >&2
  return 1
}

read_marker_at() {
  # $1 = revisión, $2 = ruta. Sin el fichero (primer día) cuenta como vacío.
  git show "$1:$2" 2>/dev/null | tr -d '\r\n' || true
}

require_output() {
  if [ -z "${GITHUB_OUTPUT:-}" ]; then
    echo "GITHUB_OUTPUT no está definido." >&2
    exit 1
  fi
}

validate_marker() {
  case "$1" in
    ""|/*|*..*)
      echo "El marcador tiene que ser una ruta relativa dentro del repo, sin '..'." >&2
      exit 2
      ;;
  esac
}

# Solo lectura. run=false cuando un schedule ya no debe seguir.
peek_day() {
  local marker today event already
  marker="$1"
  validate_marker "$marker"
  require_output
  cd_workspace
  require_branch

  today="$(TZ=Europe/Madrid date +%F)"
  event="${GITHUB_EVENT_NAME:-}"
  echo "Hora en Madrid: $(TZ=Europe/Madrid date '+%F %H:%M %Z'). Evento: ${event:-desconocido}."

  git fetch -q origin "$branch"
  already="$(read_marker_at "origin/${branch}" "$marker")"
  echo "Marcador origin/${branch}:${marker} = ${already:-<vacío>}."

  if [ "$event" = "workflow_dispatch" ]; then
    echo "Disparo manual: se prepara el trabajo aunque el marcador sea ${already:-vacío}."
    echo "run=true" >> "$GITHUB_OUTPUT"
    return 0
  fi
  if [ "$already" = "$today" ]; then
    echo "Hoy ($today) ya está reservado. No se repite la llamada a la API."
    echo "Para forzar otra ejecución: Run workflow (workflow_dispatch)."
    echo "run=false" >> "$GITHUB_OUTPUT"
    return 0
  fi
  echo "El $today aún no está reservado."
  echo "run=true" >> "$GITHUB_OUTPUT"
}

claim_day() {
  local marker subject today event already current attempt claimed
  marker="$1"
  subject="$2"
  validate_marker "$marker"
  require_output
  cd_workspace
  require_branch

  today="$(TZ=Europe/Madrid date +%F)"
  event="${GITHUB_EVENT_NAME:-}"
  echo "Hora en Madrid: $(TZ=Europe/Madrid date '+%F %H:%M %Z'). Evento: ${event:-desconocido}."

  git fetch -q origin "$branch"
  already="$(read_marker_at "origin/${branch}" "$marker")"
  echo "Marcador origin/${branch}:${marker} = ${already:-<vacío>}."

  if [ "$event" != "workflow_dispatch" ] && [ "$already" = "$today" ]; then
    echo "Hoy ($today) ya está reservado. No se repite la llamada a la API."
    echo "Para forzar otra ejecución: Run workflow (workflow_dispatch)."
    echo "run=false" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  if [ "$event" = "workflow_dispatch" ] && [ "$already" = "$today" ]; then
    echo "El marcador ya es $today. El disparo manual sigue adelante."
  fi

  if [ "$already" != "$today" ]; then
    claimed=0
    attempt=1
    while [ "$attempt" -le 3 ]; do
      git fetch -q origin "$branch"
      git checkout -q -B "$branch" "origin/${branch}"
      current="$(read_marker_at HEAD "$marker")"
      if [ "$current" = "$today" ]; then
        if [ "$event" != "workflow_dispatch" ]; then
          echo "Otro run reservó $today mientras tanto. No se llama a la API."
          echo "run=false" >> "$GITHUB_OUTPUT"
          exit 0
        fi
        echo "El marcador ya es $today. El disparo manual sigue adelante."
        claimed=1
        break
      fi
      mkdir -p "$(dirname "$marker")"
      printf '%s\n' "$today" > "$marker"
      # --only evita llevarse otros ficheros que hubiera en el index.
      git add -- "$marker"
      git commit -m "$subject $today" --only -- "$marker"
      if git push origin "HEAD:${branch}"; then
        echo "Reservado $today en $marker."
        claimed=1
        break
      fi
      echo "No se pudo reservar (intento ${attempt}). Otro push ganó la carrera; se reintenta."
      attempt=$((attempt + 1))
      sleep "$attempt"
    done
    if [ "$claimed" -ne 1 ]; then
      if [ "$event" = "workflow_dispatch" ]; then
        echo "No se pudo guardar el marcador; el disparo manual sigue adelante."
      else
        echo "No se pudo reservar el $today; se aborta para no arriesgar una segunda llamada." >&2
        exit 1
      fi
    fi
  fi

  # Dejar la copia local en la punta remota para el commit de datos, si lo hay.
  if ! git fetch -q origin "$branch" || ! git checkout -q -B "$branch" "origin/${branch}"; then
    if [ "$event" = "workflow_dispatch" ]; then
      echo "No se pudo actualizar la rama; el disparo manual sigue adelante."
      echo "run=true" >> "$GITHUB_OUTPUT"
      exit 0
    fi
    echo "No se pudo dejar la rama en origin/${branch}." >&2
    exit 1
  fi

  echo "Este run hace el trabajo de $today."
  echo "run=true" >> "$GITHUB_OUTPUT"
}

usage() {
  echo "uso: claim_madrid_day.sh peek <marcador> | <marcador> <asunto> | push" >&2
  exit 2
}

case "${1:-}" in
  push)
    [ "$#" -eq 1 ] || usage
    push_head
    ;;
  peek)
    [ "$#" -eq 2 ] || usage
    peek_day "$2"
    ;;
  "")
    usage
    ;;
  *)
    [ "$#" -eq 2 ] || usage
    claim_day "$1" "$2"
    ;;
esac
