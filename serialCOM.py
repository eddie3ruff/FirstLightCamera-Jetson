import serial
import time

# Configure serial communication settings
SERIAL_PORT = '/dev/ttyACM0' 
BAUD_RATE = 115200
TIMEOUT = 1 

def main():
    ser = None
    try:
        # Open serial connection
        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT,
            xonxoff=False,  # No software flow control
            rtscts=False,   # No hardware (RTS/CTS) flow control
            dsrdtr=False    # No hardware (DSR/DTR) flow control
        )
        
        if ser.is_open:
            print(f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud.")

        while True:
            # Get command from user
            command = input("Enter command to send (type 'exit' to quit): ")
            if command.lower() == 'exit':
                print("Exiting program.")
                break
            
            # Send command
            ser.write((command + '\r\n').encode())
            print(f"Sent: {command}")

            # Wait and read response until "fli-cli>" prompt
            full_response = ""
            while True:
                line = ser.readline().decode().strip()
                if line and "fli-cli>" not in line:
                    full_response += line + " "  # Append line with a space instead of a newline
                if line.endswith("fli-cli>"):
                    break  # Exit loop once end prompt is detected
            
            # Print the response on the same line as "Received:"
            if full_response.strip():  # Only print if there's actual content
                print(f"Received: {full_response.strip()}")

    except serial.SerialException as e:
        print(f"Serial error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if ser and ser.is_open:
            ser.close()
            print("Serial connection closed.")

if __name__ == "__main__":
    main()