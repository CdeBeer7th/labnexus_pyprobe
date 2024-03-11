# pyProbe

Want to automate your data syncronisation workload with your [LabNexus](https://github.com/CdeBeer7th/labnexus_server) server?

Simply download the ipynb or Python prober, specify your data directory and Labnexus server instance, and run it.

pyProbe will automatically upload your data to the server so you don't have to!

# Setup Example:

```
python -m pip install git+"https://github.com/CdeBeer7th/labnexus_pyprobe"

python -m labnexus_pyprobe [directory] "[server:port]"
```

Now the prober will continuously monitor the specified directory for new files.

# DEV

* Check contents of files and if they changed (after overwrites).
* Able to specify certain file types only.
