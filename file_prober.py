import os
from datetime import datetime

import requests


def FileWatcher(dir_path, server_url):
    # Set the headers for the PUT request (if needed)
    # headers = {'Content-Type': 'application/octet-stream'}

    # Keep track of the last processed file
    last_processed_files = []
    # dir_path = Path(dir_path)

    print("Checking for new files in directory:", dir_path)
    while True:
        # Get a list of all files in the directory
        files = os.listdir(dir_path)
        print("Files in directory:\n", files)
        # Sort files by modification time (newest first)
        files.sort(
            key=lambda x: os.path.getmtime(os.path.join(dir_path, x)),
            reverse=True,
        )

        # Check for new files
        for file_name in files:
            file_path = os.path.join(dir_path, file_name)

            # Skip the last processed file and directories
            if file_name in last_processed_files or os.path.isdir(file_path):
                continue

            try:
                # Open the file in binary mode
                print("Found new file", file_name)
                print(file_path)
                with open(file_path, "rb") as file:
                    files = {"file": file}
                    # Send a PUT request to the server with the file data
                    response = requests.post(server_url, files=files)

                # Check if the request was successful
                if response.status_code == 200:
                    print(f"File {file_name} uploaded successfully at {datetime.now()}")
                    last_processed_files.append(file_name)
                else:
                    print(
                        f"Error uploading file {file_name}: {response.status_code} - {response.text}",
                    )
                    print(response.__dict__)

            except Exception as e:
                print(f"Error processing file {file_name}: {e}")

            # Exit the loop after processing the newest file
            break

        # Wait for a specified time before checking again
        # You can adjust the sleep time based on your requirements
        import time

        time.sleep(5)  # Wait for 1 minute


FileWatcher(
    dir_path="/home/cdb/Workspaces/minimal_template/labnexus_server/local_scripts/testfiles",
    server_url="http://127.0.0.1:8000/files/upload/raw",
)
