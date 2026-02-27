from scanners.github_scanner import GitHubScanner
from scanners.sourcegraph_scanner import SourcegraphScanner
from scanners.urlscan_scanner import UrlscanScanner
from scanners.serper_scanner import SerperScanner
from scanners.publicwww_scanner import PublicWWWScanner

ALL_SCANNERS = [
    GitHubScanner,
    SourcegraphScanner,
    UrlscanScanner,
    SerperScanner,
    PublicWWWScanner,
]
