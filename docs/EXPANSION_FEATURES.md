# Expansion settings audit

The expansion uses its own battle, evolution, field-move and follower systems.
A shared OPTIONS entry does not supply a game implementation by itself. The
expansion patch connects these controls to the systems below; the canonical
port supplies the remaining renderer, clock and inventory controls.

| Control | Expansion implementation |
| --- | --- |
| EXP for catching | `battle_main.c`: set `expOnCatch` from `exp_catch`; native catch script awards EXP. |
| Party EXP share | `battle_util.c`: `IsGen6ExpShareEnabled` accepts `exp_share`; native eligibility, rewards and team message remain in use. The native Exp. Share item also remains functional. |
| Trade evolutions at level 40 | `pokemon.c`: normal level evolution accepts trade methods at level 40. Native extra requirements and held-item consumption remain; the partner-species condition is waived for the level alternative. |
| HMs without learning | `scrcmd.c` and `field_player_avatar.c`: eligible non-egg party members can use field HMs. Native badge/map checks remain. Fly and Flash can be used from their HMs in the bag; cancelling Fly returns to the bag. |
| Visible wild Pokémon | `wild_encounter.c`: wandering party icons, time-dependent land tables, water tables, repel checks, stable identity and native movesets. FEW/SOME supplement random encounters; MANY replaces normal walking/surf encounters. Icons survive menu/battle returns and connected-map scrolling. |
| IVs and EVs | `pokemon_summary_screen.c`: enables the native skills-page IV/EV mode prompt. |
| Repel prompt | `sprays.c`: enables the native repel/lure reuse menu; OFF only displays the expiry message. |
| Infinite TMs | `party_menu.c`: successful TM teaching retains the item. |
| Run indoors | `bike.c`: permits indoor running while retaining tile and movement restrictions. |
| Physical/special split | Always active natively. OPTIONS displays `ON (NATIVE)`; it is not a switch. Category graphics use expansion's 16x16 format. |
| Wild encounter rate | `wild_encounter.c`: scales random checks and visible spawn timing using NORMAL/OFF/¼/½/2×. |
| Shiny odds | `pokemon.c`: native player-owned generation uses the selected threshold. Native shiny locks, forced flags and bonus rolls remain. Visible encounters retain the shiny state shown before battle. |
| Always catch | `battle_script_commands.c`: native permitted ball throws take the guaranteed capture path. Trainer/tutorial restrictions remain. |
| Instant victory | `battle_main.c` and native battle script: faint opponents using the native HP, faint, EXP and replacement flow. Disabled for link, recorded, Safari and catching tutorials. |
| Fast eggs | `daycare.c`: a valid party egg becomes hatchable after five checked steps. Bad eggs are excluded. |
| Infinite money | `money.c`: reading the player's wallet restores its encrypted balance to the native maximum; other wallets remain untouched. |
| Follower | `event_object_movement.c`: controls the native following Pokémon object. Map/menu reload behavior remains native; settings changes refresh it in the overworld. Species without art use the expansion's substitute fallback. |
| Day/night | Shared renderer controls tinting in 2D and voxel views. Native DNS tint is disabled in the port to prevent double tinting. Gameplay still uses the native time-of-day calculation. |
| Clock speed | Shared clock feeds expansion time-of-day calculations. `clock.c` applies accelerated berry minutes; `overworld.c` drains them while standing still. `save.c` flushes and saves/restores the clock sidecar at save boundaries. |
| Timed wild Pokémon | Shared Hoenn timed substitutions apply to the expansion land tables, with species guards. Unmatched slots and maps retain their native encounters. |
| Show clock | Shared bottom UI displays the shared game clock. |
| Speed / real-time pacing | Shared platform frame pacing and audio clock. |
| 3D world/depth, voxel, battle 3D and other visual settings | Shared renderer/platform controls; independent of the game recipe. |
| Hoenn/national Pokédex actions | Shared extras call expansion's native Pokédex setters and unlock function. |
| Give items | Shared extras use expansion item-pocket/name/bag-capacity APIs, including single key items. |

## Verification

`builder/tests/test_expansion_enhancements.py` executes extracted production
adapters against controlled game state and checks that every gameplay toggle
has an expansion reader outside the menu registration. CI runs it after the
expansion bootstrap. Both native builds run `make verify`, including asset
and link audits. The expansion link audit requires the visible encounter
lifecycle functions, and checks the complete icon/terrain palette groups.

These checks do not replace gameplay testing on a 3DS. In particular, new
follower art, menus, field HMs, trainer transitions and save/reload behavior
still need physical-console regression testing with the deployed build.
