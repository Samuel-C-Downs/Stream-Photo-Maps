# Photo Map

See your photos on a map. Photo Map reads the location saved in each photo by your device, puts the photos on an OpenStreetMap map, and lets you click them to view them, filter them by date or phone, and tag them (for example with a project name).

Everything runs locally except for fetching map tiles (per OSM usage policy).

---

## Using Photo Map

**Map.** Numbered circles are groups of photos; click one to zoom in. Click a dot to see the photo, then **View photo** to see it large. Use the ← and → keys to step through the photos in view, and Esc to close. The filters on the left (date, tags, folder, phone) change the map straight away. The panel on the right shows the photos currently on screen.

**Review.** Some photos have no location (location was off, or the photo came from a chat app). The **Review** tab lists them. For each photo you can:

- accept a **suggested spot** (from a photo taken within a few minutes),
- click the map to drop a pin and choose **Place at pin**,
- click a grey dot (another photo from the same day) to use its spot, or
- **Skip** it.

**Undo** reverses the last action.

**Tags.** Open the **Tags** tab, select photos (click, shift-click for a range, or **Select all matching**), type a tag such as a project name or number, and click **Add to selected**. Back on the map, use the **Tags** filter to show just those photos. You can also add a tag to one photo in the large viewer.

Tags and hand-placed locations are saved by Photo Map, not in the photos themselves.

---

## If something goes wrong

| What you see | What to do |
| --- | --- |
| *"Windows protected your PC"* | Click **More info**, then **Run anyway**. |
| *"Couldn't install uv"* | Check you're online and try again. Some work computers block installs; try a personal computer. |
| The browser didn't open | Open your browser and go to **http://127.0.0.1:8050** |
| The map is grey but the dots show | The map pictures come from the internet. Check your connection. |
| *"Photo folder not found"* | The folder was moved or renamed. Click **Change folder** at the top. |
| Some photos aren't on the map | Look in the **Review** tab: they have no location. Videos and RAW files aren't supported yet. |
| New photos don't appear | Click **Re-scan folder** at the top (it also happens each time Photo Map starts). |
| Anything else | Close the black window and start again. If it still happens, send Sam a photo of the black window and the file `data\photo-map.log` from the Photo Map folder. |

**Starting completely fresh:** close Photo Map and delete the `data` folder inside the Photo Map folder. This removes your tags and hand-placed locations too, but not your photos.

---

## Privacy

- Photo Map only runs on your computer and can't be reached from other devices.
- Your photos are only read, never uploaded or changed.
- The only thing downloaded while you use it is the map background, from OpenStreetMap.

---

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, used under the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
