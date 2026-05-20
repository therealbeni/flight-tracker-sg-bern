import srtm

elevation_data = srtm.get_data()

msl_terrain = elevation_data.get_elevation(46.5542, 7.3807)
print(f"Terrain height: {msl_terrain} meters")