"""Native TikTok engagement executor (like / comment / follow) on the Following feed.

This module drives engagement on the iPad via WDA using EXACT AX-tree
coordinates captured from a live TikTok session (810x1080 WDA space):

    feedLikeButton      @ (766, 610)
    feedCommentButton    @ (766, 692)
    feedFavoriteButton   @ (766, 775)
    feedShareButton      @ (753, 857)
    friends (Following)  @ (262, 1055)   <- bottom tab = Following/Friends feed
    top_tabs_recomend    @ (577, 42)    <- For You (top)

IMPORTANT: this executor NEVER launches WDA. The client is passed in.
"""

from __future__ import annotations

import logging
import random
import time
import re
from dataclasses import dataclass, field
from typing import Optional
import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)

# --- Verified AX coordinates (810x1080 WDA space) ---------------------------
COORD = {
    "feedLikeButton": (766, 610),
    "feedCommentButton": (766, 692),
    "feedFavoriteButton": (766, 775),
    "feedShareButton": (753, 857),
    "following_tab": (262, 1055),
    "foryou_tab": (577, 42),
}
SCREEN_W, SCREEN_H = 810, 1080

AD_MARKERS = {"ad", "sponsored", "promoted", "learn more", "shop now", "install", "get app", "about this ad", "why am i seeing this ad", "subscribe", "download"}

@dataclass
class EngagementConfig:
    """Human-like engagement parameters."""
    cycles: int = 5
    min_cycles: int = 50
    duration_min: float = 15.0
    like_prob: float = 0.05
    like_cap: int = 3
    comment_prob: float = 0.0
    follow_prob: float = 0.0
    comments: list[str] = field(default_factory=lambda: [
        "this is fire", "obsessed with this", "underrated",
        "need this on repeat", "absolute banger", "goosebumps fr",
    ])
    jitter_px: int = 10
    watch_base_range: tuple[float, float] = (8.0, 25.0)
    mode: str = "following"            # "following" | "hashtag"
    hashtags: list[str] = field(default_factory=list)
    behavior_profile: dict = field(default_factory=dict)
    scroll_count: int = 20             # how many videos to scroll in a hashtag feed
    hashtag_like_prob: float = 0.2     # % likelihood to like a hashtag video


class EngagementExecutor:
    """Drives like/comment/follow on the TikTok Following feed via WDA."""

    def __init__(self, client):
        self.c = client
        self._jitter = 10
        self.cycle_log = []

    def is_ad(self, src: str) -> bool:
        """Return True if the current TikTok screen source looks like a promoted/ad video or an off-feed destination we must not engage with."""
        low = (src or "").lower()
        return any(marker in low for marker in AD_MARKERS)

    def ensure_on_feed(self) -> bool:
        """If we are not on a normal video feed (no feedLikeButton visible), try to get back. Returns True if we appear to be on a feed video."""
        try:
            src = self.c.source()
        except Exception:
            return False
        if "feedLikeButton" in src:
            return True
        # We strayed (product page / profile / webview / ad destination). Recover.
        logger.warning("Not on feed video (feedLikeButton missing) — recovering via ensure_feed()")
        if self.ensure_feed():
            return True
        return False

    def _tap(self, x: int, y: int, wait: float = 1.2):
        jx = random.randint(-self._jitter, self._jitter)
        jy = random.randint(-self._jitter, self._jitter)
        self.c.tap(x + jx, y + jy)
        time.sleep(wait)

    def _swipe_next(self):
        self.c.swipe(405, 900, 405, 200, 0.25)
        
    def _parse_source(self):
        """Parse WDA XML source safely."""
        src = self.c.source()
        try:
            return ET.fromstring(src.encode('utf-8'))
        except ET.ParseError:
            try:
                # Fallback for mock XML which might lack a root element
                return ET.fromstring(f"<root>{src}</root>".encode('utf-8'))
            except Exception as e:
                logger.error(f"XML parse failed: {e}")
                return None

    def clear_modals(self) -> int:
        """Dismiss intercepting sheets before every action. Returns dismissed count."""
        cleared = 0
        dismiss_targets = ["Save draft", "Edit", "Find contacts", "Don't allow", "Not now", "Switch account", "Add account", "Log in", "Close"]
        
        for _ in range(4):
            src = self.c.source()
            dismissed = False
            
            # Dismiss targets based on string logic
            for target in dismiss_targets:
                for line in src.split('\n'):
                    if target in line and 'visible="true"' in line and "Button" in line:
                        m = re.search(r'x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', line)
                        if m:
                            cx = int(m.group(1)) + int(m.group(3)) // 2
                            cy = int(m.group(2)) + int(m.group(4)) // 2
                            self._tap(cx, cy, wait=1.0)
                            cleared += 1
                            dismissed = True
                            break
                if dismissed:
                    break
                    
            if dismissed:
                continue
                
            # If comment sheet is visible but no feedLikeButton
            has_textfield = any(("TextField" in l or "TextView" in l) and 'visible="true"' in l for l in src.split('\n'))
            has_like = "feedLikeButton" in src
            
            if has_textfield and not has_like:
                self.c.swipe(405, 500, 405, 200, 0.3)
                time.sleep(1.0)
                cleared += 1
                continue
                
            break
            
        return cleared

    def go_following_feed(self) -> bool:
        """Switch to the Following (Friends) feed."""
        self._tap(*COORD["following_tab"], wait=2.5)
        src = self.c.source()
        return "friends" in src

    def like_current(self) -> bool:
        """Tap feedLikeButton, verify via AX."""
        src = self.c.source()
        if self.is_ad(src):
            logger.info("Skipping engage action on ad")
            return False
        self._tap(*COORD["feedLikeButton"], wait=1.5)
        src = self.c.source()
        liked = any(
            ("Video liked" in l or "Selected" in l)
            for l in src.split("\n") if "feedLikeButton" in l
        )
        time.sleep(random.uniform(0.3, 0.8))
        return liked

    def comment_current(self, text: str) -> bool:
        """Tap comment button, enter text, post, dismiss sheet."""
        src = self.c.source()
        if self.is_ad(src):
            logger.info("Skipping engage action on ad")
            return False
        self._tap(*COORD["feedCommentButton"], wait=2.5)
        
        src = self.c.source()
        tapped_field = False
        
        for line in src.split('\n'):
            if ("TextField" in line or "TextView" in line) and 'visible="true"' in line:
                m = re.search(r'x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', line)
                if m:
                    self._tap(int(m.group(1)) + int(m.group(3)) // 2,
                              int(m.group(2)) + int(m.group(4)) // 2, wait=1.0)
                    tapped_field = True
                    break
                    
        self.c.send_keys(text)
        time.sleep(1.5)
        
        src = self.c.source()
        post_tapped = False
        for target in ["Post", "Send"]:
            for line in src.split('\n'):
                if target in line and 'visible="true"' in line and "Button" in line:
                    m = re.search(r'x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', line)
                    if m:
                        self._tap(int(m.group(1)) + int(m.group(3)) // 2,
                                  int(m.group(2)) + int(m.group(4)) // 2, wait=1.5)
                        post_tapped = True
                        break
            if post_tapped:
                break
                
        if not post_tapped:
            # Fallback coordinate
            self._tap(760, 1040, wait=1.5)
            
        time.sleep(2.0)
        self.c.swipe(405, 500, 405, 200, 0.3)
        time.sleep(1.5)
        return True

    def follow_current(self) -> bool:
        """Tap visible Follow button (not Following)."""
        src = self.c.source()
        if self.is_ad(src):
            logger.info("Skipping engage action on ad")
            return False
        for line in src.split('\n'):
            if 'visible="true"' in line and "Button" in line and "Follow" in line and "Following" not in line:
                m = re.search(r'x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', line)
                if m:
                    self._tap(int(m.group(1)) + int(m.group(3)) // 2,
                              int(m.group(2)) + int(m.group(4)) // 2, wait=1.5)
                    return True
        return False

    def ensure_feed(self, max_retries: int = 3) -> bool:
        """Ensure TikTok is in the foreground and on the Following feed."""
        bundle = "com.zhiliaoapp.musically"
        for _ in range(max_retries):
            try:
                self.c.app_terminate(bundle)
            except Exception as e:
                logger.warning(f"app_terminate failed: {e}")
            time.sleep(2.0)
            
            try:
                self.c.app_start(bundle)
            except Exception as e:
                logger.warning(f"app_start failed: {e}")
            time.sleep(3.0)
            
            src = self.c.source()
            
            if "feedLikeButton" not in src:
                try:
                    self.c.app_start(bundle)
                except Exception as e:
                    logger.warning(f"app_start failed: {e}")
                time.sleep(3.0)
                src = self.c.source()
                
            if "feedLikeButton" in src:
                self.go_following_feed()
                src = self.c.source()
                if "friends" not in src:
                    self._swipe_next()
                    time.sleep(1.0)
                    src = self.c.source()
                return True
                
        logger.error("Failed to foreground TikTok: feedLikeButton not visible after retries.")
        return False

    def search_hashtag(self, tag: str) -> bool:
        src = self.c.source()
        tapped_search = False
        for line in src.split('\n'):
            if 'visible="true"' in line and "Button" in line and ("Search" in line or "search" in line):
                m = re.search(r'x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', line)
                if m:
                    cx = int(m.group(1)) + int(m.group(3)) // 2
                    cy = int(m.group(2)) + int(m.group(4)) // 2
                    self._tap(cx, cy, wait=1.5)
                    tapped_search = True
                    break
        if not tapped_search:
            self._tap(770, 42, wait=1.5)
            
        self.c.send_keys(tag)
        self.c.send_keys("\n")
        time.sleep(2.5)
        
        src = self.c.source()
        if "feedLikeButton" in src or any("Cell" in l for l in src.split('\n')):
            return True
        return False

    def run_hashtag_feed(self, cfg) -> dict:
        stats = {"mode": "hashtag", "hashtags": cfg.hashtags, "liked": 0, "scrolled": 0, "errors": 0}
        for tag in cfg.hashtags:
            if not self.search_hashtag(tag):
                stats["errors"] += 1
                continue
            for i in range(cfg.scroll_count):
                self.clear_modals()
                
                src = self.c.source()
                if self.is_ad(src):
                    logger.info("cycle %s: ad detected, skipping without engaging", i)
                    self._swipe_next()
                    if not self.ensure_on_feed():
                        break
                    stats["scrolled"] += 1
                    continue
                    
                nominal = random.uniform(*cfg.watch_base_range)
                mult = random.choice([0.6, 1.0, 1.2, random.uniform(0.5, 1.5)])
                time.sleep(nominal * mult)
                if random.random() < cfg.hashtag_like_prob:
                    if self.like_current():
                        stats["liked"] += 1
                self._swipe_next()
                if not self.ensure_on_feed():
                    break
                stats["scrolled"] += 1
            self.go_following_feed()
        return stats

    def run(self, cfg: Optional[EngagementConfig] = None) -> dict:
        """Orchestrate N cycles of engagement."""
        cfg = cfg or EngagementConfig()
        self._jitter = cfg.jitter_px
        stats = {"cycles": 0, "liked": 0, "commented": 0, "followed": 0, "errors": 0}
        
        if cfg.mode == "hashtag" and cfg.hashtags:
            if not self.ensure_feed():
                return stats
            return self.run_hashtag_feed(cfg)

        start = time.time()
        end_time = start + cfg.duration_min * 60
        if not self.ensure_feed():
            return stats
            
        while stats["cycles"] < cfg.min_cycles:
            if time.time() > end_time + 120:  # 2-min grace past duration, then stop
                break
            try:
                self.clear_modals()
                
                src = self.c.source()
                i = stats["cycles"]
                if self.is_ad(src):
                    logger.info(f"cycle {i}: ad detected, skipping without engaging")
                    self._swipe_next()
                    if not self.ensure_on_feed():
                        break
                    stats["cycles"] += 1
                    continue

                current_author = None
                for line in src.split('\n'):
                    if 'visible="true"' in line:
                        m = re.search(r'name="(@[\w.]+)"', line)
                        if m:
                            current_author = m.group(1)
                            break
                
                i = stats["cycles"]
                try:
                    self.c.screenshot(f"/tmp/engage_cycle_{i}.png")
                except Exception as e:
                    logger.warning(f"Screenshot failed: {e}")
                    
                logger.info(f"cycle {i}: author={current_author}")
                cycle_entry = {"cycle": i, "author": current_author, "liked": False, "followed": False}
                self.cycle_log.append(cycle_entry)
                
                # Human-like watch
                nominal = random.uniform(*cfg.watch_base_range)
                mult = random.choice([0.6, 1.0, 1.2, random.uniform(0.5, 1.5)])
                time.sleep(nominal * mult)
                
                if random.random() < cfg.like_prob and stats["liked"] < cfg.like_cap:
                    if self.like_current():
                        stats["liked"] += 1
                        cycle_entry["liked"] = True
                        logger.info(f"cycle {i}: liked")
                        
                if random.random() < cfg.comment_prob:
                    if self.comment_current(random.choice(cfg.comments)):
                        stats["commented"] += 1
                        logger.info(f"cycle {i}: commented")
                        
                if random.random() < cfg.follow_prob:
                    if self.follow_current():
                        stats["followed"] += 1
                        cycle_entry["followed"] = True
                        logger.info(f"cycle {i}: followed")
                        
                # Swipe up to next video
                self._swipe_next()
                if not self.ensure_on_feed():
                    break
                stats["cycles"] += 1
                
            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"cycle {stats['cycles']} error: {e}")
                try:
                    self.c.status()
                except Exception:
                    logger.error("WDA down — attempting recovery via ensure_feed")
                    if not self.ensure_feed(max_retries=1):
                        break
                    
        return stats


def run_engagement(client, cfg: Optional[EngagementConfig] = None) -> dict:
    """Convenience entry point."""
    return EngagementExecutor(client).run(cfg)
