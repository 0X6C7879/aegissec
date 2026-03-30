import { create } from "zustand";
import type {
  AttachmentMetadata,
  SessionEventEntry,
} from "../types/sessions";

type DraftAttachmentForm = {
  name: string;
  contentType: string;
  sizeBytes: string;
};

type SessionDraftState = {
  content: string;
  attachmentForm: DraftAttachmentForm;
  attachments: AttachmentMetadata[];
};

type UiState = {
  includeDeleted: boolean;
  isEventPanelOpen: boolean;
  lastVisitedSessionId: string | null;
  draftsBySession: Record<string, SessionDraftState>;
  eventsBySession: Record<string, SessionEventEntry[]>;
  setIncludeDeleted: (value: boolean) => void;
  toggleEventPanel: () => void;
  setLastVisitedSessionId: (sessionId: string | null) => void;
  setDraftContent: (sessionId: string, content: string) => void;
  updateAttachmentForm: (
    sessionId: string,
    field: keyof DraftAttachmentForm,
    value: string,
  ) => void;
  addDraftAttachment: (sessionId: string) => boolean;
  removeDraftAttachment: (sessionId: string, attachmentId: string) => void;
  clearDraft: (sessionId: string) => void;
  appendEvent: (sessionId: string, event: SessionEventEntry) => void;
};

function byCreatedAt(left: SessionEventEntry, right: SessionEventEntry): number {
  return new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime();
}

const defaultAttachmentForm = (): DraftAttachmentForm => ({
  name: "",
  contentType: "application/octet-stream",
  sizeBytes: "0",
});

const defaultDraftState = (): SessionDraftState => ({
  content: "",
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
  draftsBySession: {},
  eventsBySession: {},
  setIncludeDeleted: (value) => set({ includeDeleted: value }),
  toggleEventPanel: () => set((state) => ({ isEventPanelOpen: !state.isEventPanelOpen })),
  setLastVisitedSessionId: (sessionId) => set({ lastVisitedSessionId: sessionId }),
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
      const contentType = draftState.attachmentForm.contentType.trim() || "application/octet-stream";
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
            attachments: draftState.attachments.filter((attachment) => attachment.id !== attachmentId),
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
  appendEvent: (sessionId, event) =>
    set((state) => {
      const currentEvents = state.eventsBySession[sessionId] ?? [];
      const nextEvents = [...currentEvents, event].sort(byCreatedAt).slice(-50);

      return {
        eventsBySession: {
          ...state.eventsBySession,
          [sessionId]: nextEvents,
        },
      };
    }),
}));
