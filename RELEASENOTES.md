# New Features

* Crossed Dungeon generation improvements
* Standard mode generation improvements
* Spoiler lists bosses (multiworld compatible)
* Bombs escape not valid for Crossed Dungeon
* Graph algorithm speed improvement for placements and playthrough
* TT Attic Hint tile should have a crystal switch accessible now
* Updated to v.31.0.5

### Experimental features

* Moved BK information and total chest keys per dungeon to keysanity menu. The info there requires compass for all info.
* Map still required for on-hud key counter.
* Added total counter to keysanity the compass/map screen when you have the compass for the dungeon.
* Open "Edge" transitions can now be linked with normal doors
* "Straight" staircases (the ones similar to normal doors) can be linked with both normal doors and edges

#### Couple of temporary debug features added:

* Total item count displays where TFH's goal usually does
* A red square appears in the upper right corner of the hud if the castle gate is closed      

# Bug Fixes

* Fix for Animated Tiles in crossed dungeon
* Stonewall hardlock no longer reachable from certain drops (Sewer Drop, some Skull Woods drops) that were previously possible
* No logic uses less key door logic
* Spoiler log encoding
* Enemizer settings made consistent with website
* Swamp flooded ladders in the basement now requires Flippers
* PoD EG Glitch gets killed on transitions (Only when DR is on)
* Problem with standard logic fixed wanting you to pass through the tapestry backwards to rescue Zelda
* Fixed SRAM corruption issues
* Problem with the dungeons requiring you to take Blind through her attic fixed. (Maiden no longer despawns)
* Hyrule Castle will not be your DW access in various Entrance Shuffles: simple, restricted, dungeonssimple, dungeonsfull 
(Also prevents getting stuck in TR opening)
* Beatable only (accessibility: none) no longer fails when there are unplaced items