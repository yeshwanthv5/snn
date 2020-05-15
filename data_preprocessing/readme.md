## MNIST-DVS Preprocessing steps
* Download dataset from http://www2.imse-cnm.csic.es/caviar/MNIST_DVS/
  * Takes awfully long time. Server will be unreachable more often than not. Handy command `wget -m http://www2.imse-cnm.csic.es/caviar/MNIST_DVS/` mirrors the dataset from the server.
  * Unzip the `grabbed_data*.zip` files.
* Run matlab prcessing script.
  * Install octave `brew install octave`.
  * Run `run("process_mnist.m")` from octave command prompt. This assumes the data is present in the current directory.
  * This creates `processed_data*/` directories in your home directory and creates data in `.aedat` format
