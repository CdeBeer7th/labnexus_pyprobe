# pyProbe

Want to automate your data syncronisation workload with your [LabNexus](https://github.com/CdeBeer7th/labnexus_server) server?

Simply download the ipynb or Python prober, specify your data directory and Labnexus server instance, and run it.

pyProbe will automatically upload your data to the server so you don't have to!

# Setup Example:

Bash:

```
python -m pip install git+"https://github.com/CdeBeer7th/labnexus_pyprobe"
python -m labnexus_pyprobe [directory] "[server:port]"
```

Stout:

> Enter your LabNexus email here: me@email.com
>
>
> Enter your account password here: StRoNgPa$$

Now the prober will continuously monitor the specified directory for new files and upload them to the server.

Just kill the process when it's not needed. On restart, a new session is created.

# DEV

- Check contents of files and if they changed (after overwrites).
- Able to specify certain file types only.
- store login vars?
