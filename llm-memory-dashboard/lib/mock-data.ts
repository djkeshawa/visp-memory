import type { Memory, Intent, Stats, SystemStatus } from "./types"

export const mockStats: Stats = {
  totalMemories: 1247,
  activeIntents: 8,
  knowledgeNodes: 342,
  connections: 1893,
}

export const mockMemories: Memory[] = [
  {
    id: "1",
    content:
      "User decided to implement OAuth2 authentication with Google and GitHub providers for the main application.",
    layer: "episodic",
    category: "Authentication",
    createdAt: "2024-01-15T10:30:00Z",
  },
  {
    id: "2",
    content: "PostgreSQL was chosen as the primary database due to its robust JSON support and reliability.",
    layer: "semantic",
    category: "Database",
    createdAt: "2024-01-14T14:20:00Z",
  },
  {
    id: "3",
    content: "Complete the API documentation before the next sprint review meeting.",
    layer: "intent",
    category: "Documentation",
    createdAt: "2024-01-13T09:15:00Z",
  },
  {
    id: "4",
    content: "React Query should be used for server state management across all data fetching operations.",
    layer: "semantic",
    category: "State Management",
    createdAt: "2024-01-12T16:45:00Z",
  },
  {
    id: "5",
    content: "User prefers Tailwind CSS for styling due to its utility-first approach and excellent documentation.",
    layer: "episodic",
    category: "Styling",
    createdAt: "2024-01-11T11:00:00Z",
  },
  {
    id: "6",
    content: "Implement rate limiting for all public API endpoints to prevent abuse.",
    layer: "intent",
    category: "Security",
    createdAt: "2024-01-10T13:30:00Z",
  },
  {
    id: "7",
    content: "The application architecture follows a modular monolith pattern for easier initial development.",
    layer: "semantic",
    category: "Architecture",
    createdAt: "2024-01-09T08:00:00Z",
  },
  {
    id: "8",
    content: "User mentioned wanting to migrate to microservices once the team scales beyond 10 developers.",
    layer: "episodic",
    category: "Architecture",
    createdAt: "2024-01-08T15:20:00Z",
  },
]

export const mockIntents: Intent[] = [
  {
    id: "1",
    description: "Implement user profile settings page with avatar upload",
    priority: "high",
    status: "active",
    createdAt: "2024-01-15T10:00:00Z",
  },
  {
    id: "2",
    description: "Add email notification system for important events",
    priority: "medium",
    status: "active",
    createdAt: "2024-01-14T09:00:00Z",
  },
  {
    id: "3",
    description: "Create dashboard analytics for user engagement metrics",
    priority: "high",
    status: "active",
    createdAt: "2024-01-13T14:00:00Z",
  },
  {
    id: "4",
    description: "Set up CI/CD pipeline with automated testing",
    priority: "high",
    status: "completed",
    createdAt: "2024-01-10T08:00:00Z",
  },
  {
    id: "5",
    description: "Configure production database backups",
    priority: "medium",
    status: "completed",
    createdAt: "2024-01-08T11:00:00Z",
  },
  {
    id: "6",
    description: "Implement dark mode support across the application",
    priority: "low",
    status: "completed",
    createdAt: "2024-01-05T16:00:00Z",
  },
]

export const mockSystemStatus: SystemStatus = {
  apiServer: "online",
  vectorDatabase: "ready",
  embeddings: "active",
  codexMcp: "available",
}
