# DialBench

DialBench is a local photo inspection desk for analog pointer dials. Start with
`./start.sh`, then open [the inspection desk](http://127.0.0.1:19891/).
Stop the service with `./start.sh stop`. The Compose named volume retains
imported photos, calibration, and saved inspections.

1. Open the clearly marked synthetic sample or import one PNG or JPEG.
   An aligned, same-sized reference photo can be added when you import.
2. Select the dial center, the low tick, and the high tick on the original photo
   with the image toolbar. Set their numeric values and sweep direction.
3. Select Pointer and mark a visible point on the needle to take a reading.
   Use Pixel probe or the coordinate form to check an exact source location.
4. Inspect radial luminance, a scale-band trace, exposure clipping, or brightness
   spread. Click a profile row to see which source pixel produced it.
   A scale-band scan can draw from both aligned photos: **Suggest split** keeps
   the longest readable stretch of one photo, hands stretches the other photo
   reads better to that one, and leaves stretches neither can read blank so you
   know what to reshoot. **Use suggested photo split** adopts the allocation;
   each row then names its photo, and **Photo** on a row (or "Show photo")
   reopens that original at the row's pixel.
5. Save calibration for future sessions or save an inspection snapshot.
   A snapshot reopens its original source, with downloads for CSV and marked PNG.
6. Use the Photo preparation workspace to process a copy of a photo and attach
   an output as an aligned reference. Preserve the full dimensions and the dial
   position in both images.

Photographs are decoded to RGB in their displayed EXIF orientation, stored as
immutable PNGs, and limited to 14 MiB and four million pixels per upload.
The scale uses a circular, linear angular calibration intended for straight-on
photos. DialBench does not correct parallax or perspective and does not verify an
instrument for operational use. The built-in images are synthetic fixtures.
