multi-source-gauss-bumps:
	python multi_source_wave_scattering.py -l 4 -p 16 -k 40 --dirs 100 --debug
	ffmpeg -framerate 20 -i data/multi_source_wave_scattering/gauss_bumps_k_40/multi_source_wave_scattering_%d.png -c:v libx264 -pix_fmt yuv420p data/multi_source_wave_scattering/movie_k_40_gauss_bumps.mp4 -y

multi-source-GBM_1:
	python multi_source_wave_scattering.py -l 3 -p 16 -k 40 --dirs 100 --debug --scattering_potential GBM_1
	ffmpeg -framerate 12 -i data/multi_source_wave_scattering/GBM_1_k_40/multi_source_wave_scattering_%d.png -c:v libx264 -pix_fmt yuv420p data/multi_source_wave_scattering/movie_k_40_GBM_1.mp4 -y

multi-source-vertically_graded:
	python multi_source_wave_scattering.py -l 3 -p 16 -k 40 --dirs 100 --debug --scattering_potential vertically_graded
	ffmpeg -framerate 12 -i data/multi_source_wave_scattering/vertically_graded_k_40/multi_source_wave_scattering_%d.png -c:v libx264 -pix_fmt yuv420p data/multi_source_wave_scattering/movie_k_40_vertically_graded.mp4 -y