import { create } from "zustand";
import { mergeSessionEventEntries } from "../lib/sessionUtils";
import type { AttachmentMetadata, SessionEventEntry } from "../types/sessions";

type DraftAttachmentForm = {
  name: string;
  contentType: string;
  sizeBytes: string;
};

type SessionDraftState = {
  content: string;
  queuedContent: string;
  queuedReady: boolean;
  attachmentForm: DraftAttachmentForm;
  attachments: AttachmentMetadata[];
};

type UiState = {
  includeDeleted: boolean;
  isEventPanelOpen: boolean;
  lastVisitedSessionId: string | null;
  themePreference: "dark" | "light";
  uiDensity: "compact" | "comfortable";
  draftsBySession: Record<string, SessionDraftState>;
  eventsBySession: Record<string, SessionEventEntry[]>;
  lastServerCursorBySession: Record<string, number>;
  setIncludeDeleted: (value: boolean) => void;
  toggleEventPanel: () => void;
  setLastVisitedSessionId: (sessionId: string | null) => void;
  setThemePreference: (value: "dark" | "light") => void;
  setUiDensity: (value: "compact" | "comfortable") => void;
  setDraftContent: (sessionId: string, content: string) => void;
  setQueuedDraftContent: (sessionId: string, content: string) => void;
  markQueuedDraftReady: (sessionId: string) => void;
  promoteQueuedDraft: (sessionId: string) => void;
  clearQueuedDraft: (sessionId: string) => void;
  updateAttachmentForm: (
    sessionId: string,
    field: keyof DraftAttachmentForm,
    value: string,
  ) => void;
  addDraftAttachment: (sessionId: string) => boolean;
  removeDraftAttachment: (sessionId: string, attachmentId: string) => void;
  clearDraft: (sessionId: string) => void;
  markEventCursorSeen: (sessionId: string, cursor: number) => boolean;
  appendEvent: (sessionId: string, event: SessionEventEntry) => boolean;
};

const defaultAttachmentForm = (): DraftAttachmentForm => ({
  name: "",
  contentType: "application/octet-stream",
  sizeBytes: "0",
});

const defaultDraftState = (): SessionDraftState => ({
  content: "",
  queuedContent: "",
  queuedReady: false,
  attachmentForm: defaultAttachmentForm(),
  attachments: [],
});

function getDraftState(
  draftsBySession: Record<string, SessionDraftState>,
  sessionId: string,
): SessionDraftState {
  return draftsBySession[sessionId] ?? defaultDraftState();
}

export const useUiStore = create<UiState>((set) => ({
  includeDeleted: false,
  isEventPanelOpen: true,
  lastVisitedSessionId: null,
  themePreference:
    typeof window !== "undefined" && window.localStorage.getItem("aegissec.ui.theme") === "light"
      ? "light"
      : "dark",
  uiDensity:
    typeof window !== "undefined" &&
    window.localStorage.getItem("aegissec.ui.density") === "comfortable"
      ? "comfortable"
      : "compact",
  draftsBySession: {},
  eventsBySession: {},
  lastServerCursorBySession: {},
  setIncludeDeleted: (value) => set({ includeDeleted: value }),
  toggleEventPanel: () => set((state) => ({ isEventPanelOpen: !state.isEventPanelOpen })),
  setLastVisitedSessionId: (sessionId) => set({ lastVisitedSessionId: sessionId }),
  setThemePreference: (value) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("aegissec.ui.theme", value);
    }
    set({ themePreference: value });
  },
  setUiDensity: (value) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("aegissec.ui.density", value);
    }
    set({ uiDensity: value });
  },
  setDraftContent: (sessionId, content) =>
    set((state) => ({
      draftsBySession: {
        ...state.draftsBySession,
        [sessionId]: {
          ...getDraftState(state.draftsBySession, sessionId),
          content,
        },
      },
    })),
  setQueuedDraftContent: (sessionId, content) =>
    set((state) => ({
      draftsBySession: {
        ...state.draftsBySession,
        [sessionId]: {
          ...getDraftState(state.draftsBySession, sessionId),
          queuedContent: content,
          queuedReady: false,
        },
      },
    })),
  markQueuedDraftReady: (sessionId) =>
    set((state) => {
      const draftState = getDraftState(state.draftsBySession, sessionId);
      if (draftState.queuedContent.trim().length === 0) {
        return state;
      }

      return {
        draftsBySession: {
          ...state.draftsBySession,
          [sessionId]: {
            ...draftState,
            queuedReady: true,
          },
        },
      };
    }),
  promoteQueuedDraft: (sessionId) =>
    set((state) => {
      const draftState = getDraftState(state.draftsBySession, sessionId);
      if (!draftState.queuedReady || draftState.queuedContent.trim().length === 0) {
        return state;
      }

      return {
        draftsBySession: {
          ...state.draftsBySession,
          [sessionId]: {
            ...draftState,
            content: draftState.queuedContent,
            queuedContent: "",
            queuedReady: false,
          },
        },
      };
    }),
  clearQueuedDraft: (sessionId) =>
    set((state) => ({
      draftsBySession: {
        ...state.draftsBySession,
        [sessionId]: {
          ...getDraftState(state.draftsBySession, sessionId),
          queuedContent: "",
          queuedReady: false,
        },
      },
    })),
  updateAttachmentForm: (sessionId, field, value) =>
    set((state) => ({
      draftsBySession: {
        ...state.draftsBySession,
        [sessionId]: {
          ...getDraftState(state.draftsBySession, sessionId),
          attachmentForm: {
            ...getDraftState(state.draftsBySession, sessionId).attachmentForm,
            [field]: value,
          },
        },
      },
    })),
  addDraftAttachment: (sessionId) => {
    let added = false;

    set((state) => {
      const draftState = getDraftState(state.draftsBySession, sessionId);
      const name = draftState.attachmentForm.name.trim();
      const contentType =
        draftState.attachmentForm.contentType.trim() || "application/octet-stream";
      const sizeBytes = Number.parseInt(draftState.attachmentForm.sizeBytes, 10);

      if (!name || Number.isNaN(sizeBytes) || sizeBytes < 0) {
        return state;
      }

      added = true;

      return {
        draftsBySession: {
          ...state.draftsBySession,
          [sessionId]: {
            ...draftState,
            attachments: [
              ...draftState.attachments,
              {
                id: crypto.randomUUID(),
                name,
                content_type: contentType,
                size_bytes: sizeBytes,
              },
            ],
            attachmentForm: defaultAttachmentForm(),
          },
        },
      };
    });

    return added;
  },
  removeDraftAttachment: (sessionId, attachmentId) =>
    set((state) => {
      const draftState = getDraftState(state.draftsBySession, sessionId);

      return {
        draftsBySession: {
          ...state.draftsBySession,
          [sessionId]: {
            ...draftState,
            attachments: draftState.attachments.filter(
              (attachment) => attachment.id !== attachmentId,
            ),
          },
        },
      };
    }),
  clearDraft: (sessionId) =>
    set((state) => ({
      draftsBySession: {
        ...state.draftsBySession,
        [sessionId]: defaultDraftState(),
      },
    })),
  markEventCursorSeen: (sessionId, cursor) => {
    let shouldApply = false;

    set((state) => {
      const currentCursor = state.lastServerCursorBySession[sessionId];
      if (typeof currentCursor === "number" && cursor <= currentCursor) {
        return state;
      }

      shouldApply = true;

      return {
        lastServerCursorBySession: {
          ...state.lastServerCursorBySession,
          [sessionId]: cursor,
        },
      };
    });

    return shouldApply;
  },
  appendEvent: (sessionId, event) => {
    let wasApplied = false;

    set((state) => {
      const currentEvents = state.eventsBySession[sessionId] ?? [];
      const nextEvents = mergeSessionEventEntries(currentEvents, event);
      const nextCursor =
        typeof event.cursor === "number" && Number.isFinite(event.cursor)
          ? event.cursor
          : state.lastServerCursorBySession[sessionId];

      wasApplied =
        nextEvents.length !== currentEvents.length ||
        nextEvents.some((item, index) => item !== currentEvents[index]);

      return {
        eventsBySession: {
          ...state.eventsBySession,
          [sessionId]: nextEvents,
        },
        lastServerCursorBySession:
          typeof nextCursor === "number" && Number.isFinite(nextCursor)
            ? {
                ...state.lastServerCursorBySession,
                [sessionId]: nextCursor,
              }
            : state.lastServerCursorBySession,
      };
    });

    return wasApplied;
  },
}));
