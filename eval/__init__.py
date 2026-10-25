"""CodeSentinel evaluation harness.

Scores six audit configurations against synthetic FHIR charts whose missed-code
labels are known before any model runs. Live Nemotron and Tavily calls are used
only when the API keys are set; otherwise the run is offline and labeled as such.
"""

__version__ = "1.0.0"
