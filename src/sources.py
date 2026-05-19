"""Source URLs the agent scrapes on each run.

Mix of US (TechCrunch) and European (EU-Startups, Maddyness, Tech.eu, Sifted)
feeds. The European tilt is deliberate — partners working off this brief care
about EU-domiciled deals first; US sources are kept for cross-border arbitrage
signal (comparing US rounds against European analogues).
"""

DEFAULT_SOURCES: list[str] = [
    "https://techcrunch.com/category/startups/",
    "https://techcrunch.com/category/artificial-intelligence/",
    "https://www.eu-startups.com/",
    "https://www.maddyness.com/uk/",
    "https://tech.eu/",
    "https://sifted.eu/",
]
