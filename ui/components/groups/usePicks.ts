"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { EMPTY_PICKS, lastTouched, loadPicks, picksReducer, savePicks, type Pick, type PicksAction, type PicksState } from "@/lib/picks";

/** Your picks for one event (this browser only), with an undo/redo stack. `onJump` gets the photo an undo or redo
 *  touched, so the view can show it. */
export function usePicks(eventId: string, onJump?: (id: string) => void) {
  const [state, raw] = useReducer(picksReducer, EMPTY_PICKS);
  const ref = useRef<PicksState>(state);
  ref.current = state;
  const loaded = useRef(false);
  const jump = useRef(onJump);
  jump.current = onJump;

  useEffect(() => {
    // Restored picks are not undoable steps.
    raw({ type: "load", picks: loadPicks(eventId) });
    loaded.current = true;
  }, [eventId]);

  useEffect(() => {
    if (loaded.current) savePicks(eventId, state.picks);
  }, [eventId, state.picks]);

  const dispatch = useCallback((a: PicksAction) => {
    const prev = ref.current;
    const next = picksReducer(prev, a);
    raw(a);
    const id = lastTouched(prev, next);
    if (id) jump.current?.(id);
    return next;
  }, []);

  return {
    picks: state.picks,
    canUndo: state.undo.length > 0,
    canRedo: state.redo.length > 0,
    set: (id: string, pick: Pick) => dispatch({ type: "set", id, pick }),
    clear: (id: string) => dispatch({ type: "clear", id }),
    undo: () => dispatch({ type: "undo" }),
    redo: () => dispatch({ type: "redo" }),
  };
}
