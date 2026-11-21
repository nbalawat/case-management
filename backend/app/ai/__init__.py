"""The AI layer's shared parts: one model gateway every model call goes through, whichever feature makes it.

  types      the provider-neutral conversation interface (tool specs, tool calls, turns)
  endpoints  the models a deployment can reach, from configuration (hosted, open-weight on vLLM or Ollama, none)
  policy     each client's model policy: which endpoint serves which purpose, where inference may run, spend limits
  usage      a metered record of every model call, and the budget checks that read it
  gateway    resolves a client's policy to a working, metered model for one purpose
"""
