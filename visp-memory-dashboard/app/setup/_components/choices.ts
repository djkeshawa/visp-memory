export type ChoiceKey = "keyword" | "ollama" | "openai" | "openrouter"

export interface SetupChoice {
  title: string
  detail: string
  tag: string
  config: string
  note?: string
}

export const CHOICE_ORDER: ChoiceKey[] = ["keyword", "ollama", "openai", "openrouter"]

export const CHOICES: Record<ChoiceKey, SetupChoice> = {
  keyword: {
    title: "Keyword search",
    detail: "Works immediately without a model or API key.",
    tag: "No setup",
    config: "VISP_MEMORY_EMBEDDING_PROVIDER=none",
  },
  ollama: {
    title: "Local Ollama",
    detail: "Generate embeddings locally with a downloaded model.",
    tag: "Runs locally",
    config: "VISP_MEMORY_EMBEDDING_PROVIDER=ollama\nVISP_MEMORY_EMBEDDING_MODEL=nomic-embed-text\nOLLAMA_HOST=http://ollama:11434",
    note: "With the project Compose files, use docker-compose.ollama.yml to download the selected model and start Ollama before the app.",
  },
  openai: {
    title: "OpenAI",
    detail: "Use your server's OpenAI key to generate embeddings.",
    tag: "Needs a provider key",
    config: "VISP_MEMORY_EMBEDDING_PROVIDER=openai\nEMBEDDING_API_KEY=<your-provider-key>",
  },
  openrouter: {
    title: "OpenRouter",
    detail: "Use your server's OpenRouter key to generate embeddings.",
    tag: "Needs a provider key",
    config: "VISP_MEMORY_EMBEDDING_PROVIDER=openrouter\nEMBEDDING_API_KEY=<your-provider-key>",
  },
}
