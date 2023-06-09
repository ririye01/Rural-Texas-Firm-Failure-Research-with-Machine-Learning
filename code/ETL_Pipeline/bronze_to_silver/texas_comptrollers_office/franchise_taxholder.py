import re
import sys
import os
import requests
import time
from threading import Thread
import multiprocessing
from queue import Queue
from socrata.authorization import Authorization
from requests import Response
from typing import Dict, List, Any
from pyspark.sql import DataFrame, SparkSession, Row



def _get_request_to_json_endpoint(
    url: str
) -> Dict[str, Any]:
    """
    Send a GET request to the JSON endpoint to retrieve all 
    relevant up-to-date franchise tax holder information. 

    Requirements
    ------------
    The following environment variables must be configured. 
    For Mac, store in `~/.zshrc` or `~/.bash_profile`:
        - SOCRATA_USERNAME
        - SOCRATA_PASSWORD
    
    Parameters
    ----------
    url: str
        The JSON endpoint for Franchise Tax Holder data.
    
    Return
    ------
    json: Dict[str, Any]
        A dictionary representing Texas franchise tax holder 
        information in a .json file format.
    """

    # Boilerplate...
    # Make an auth object
    auth: Authorization = Authorization(
        domain = url,
        username = os.getenv("SOCRATA_USERNAME"),
        password = os.getenv("SOCRATA_PASSWORD"),
    )

    params: Dict[str, int] = {"$limit": 50000, "$offset": 0}  # Set the desired limit of results per request
    data_dict: Dict[str, Any] = {}  # Dictionary to store the retrieved data

    start_time: float = time.time()

    while True:
        response: Response = requests.get(url, params=params)

        if response.status_code != 200:
            raise IOError("Failed to successfully pull up to {} attributes".format(
                params['$offset'] + params['$limit']
            ))

        response_data: Dict[str, Any] = response.json()

        print("Data successfuly pulled for first {} attributes".format(
            params['$offset'] + params['$limit']
        ))
        
        # Add the retrieved data to the dictionary
        for record in response_data:
            # Assuming there is a unique identifier in the record, use it as the key
            record_id: str = record["taxpayer_number"]
            data_dict[record_id] = record
        
        # Check if there are more results
        if len(response_data) < params["$limit"]:
            break
        
        # Set the offset for the next request
        params["$offset"] = params.get("$offset", 0) + params["$limit"]

    # Tracker variables
    end_time: float = time.time()
    execution_time: float = end_time - start_time
    dataset_size_megabytes: float = sys.getsizeof(data_dict) / (1024**2)

    # Print the number of records retrieved, execution time, and memory
    print("Total Active Franchise Tax records retrieved:", len(data_dict))
    print(f"Execution time: {execution_time:.1f} seconds")
    print(f"Dataset size: {dataset_size_megabytes} MegaBytes \n")

    # Example: Print the first record
    if data_dict:
        first_record = next(iter(data_dict.values()))
        print("First record:", first_record)
    else:
        print("No records retrieved.")

    return data_dict


def _worker(
    q: Queue, 
    result: Dict[str, Any],
) -> None:
    """
    This worker function is intended to be run in a separate thread and 
    performs GET requests to a JSON endpoint, parsing the results and 
    storing them in a shared dictionary.

    Parameters
    ----------
    q: Queue
        A queue containing tuples of (url, params) which the worker will
        use to send GET requests. The worker will continue processing
        items from the queue until it is empty.

    result: Dict[str, Any]
        A shared dictionary where the worker stores the result of each 
        GET request. The dictionary key is assumed to be a unique "taxpayer_number"
        obtained from the response, and the value is the entire record. This 
        dictionary is shared among all worker threads and is used to accumulate 
        the results.

    Note
    ----
    The worker function will continue processing items from the queue until
    the queue is empty. If an error occurs while processing an item, the
    worker will log the error and continue with the next item in the queue.
    The worker function does not return a value; all results are stored in the
    shared `result` dictionary.

    The worker function assumes that the GET request will return a JSON response
    containing a list of records, each with a unique "taxpayer_number". If the 
    response does not meet these expectations, the worker may fail with an error.
    """
    while not q.empty():
        url, params = q.get()
        try:
            response: Response = requests.get(url, params=params)
            if response.status_code != 200:
                raise IOError("Failed to successfully pull up to {} attributes".format(
                    params['$offset'] + params['$limit']
                ))

            response_data: Dict[str, Any] = response.json()

            print("Data successfuly pulled for first {} attributes".format(
                params['$offset'] + params['$limit']
            ))

            # Add the retrieved data to the dictionary
            for record in response_data:
                # Assuming there is a unique identifier in the record, use it as the key
                record_id: str = record["taxpayer_number"]
                result[record_id] = record
        except Exception as e:
            print(f'Error while processing: {str(e)}')

        q.task_done()


def _multithreaded_get_request_to_json_endpoint(
    url: str, 
    num_threads: int = 8,
) -> Dict[str, Any]:
    """
    Send a GET request to the JSON endpoint to retrieve all 
    relevant up-to-date franchise tax holder information. 

    Requirements
    ------------
    The following environment variables must be configured. 
    For Mac users, store in `~/.zshrc` or `~/.bash_profile`:
        - `SOCRATA_USERNAME`
        - `SOCRATA_PASSWORD`
    
    Parameters
    ----------
    url: str
        The JSON endpoint for Franchise Tax Holder data.
    
    Return
    ------
    json: Dict[str, Any]
        A dictionary representing Texas franchise tax holder 
        information in a .json file format.
    """
    # Make an auth object
    auth: Authorization = Authorization(
        domain = url,
        username = os.getenv("SOCRATA_USERNAME"),
        password = os.getenv("SOCRATA_PASSWORD"),
    )

    start_time: float = time.time()

    data_dict: Dict[str, Any] = {}  # Dictionary to store the retrieved data
    q: Queue = Queue()

    # Create a queue of requests
    params: Dict[str, int] = {"$limit": 50000, "$offset": 0}

    print("Retrieving Active Franchise Tax Permit Holder data...")
    print(f"Multithreading GET request to {url} across {num_threads} CPU cores...")

    while True:
        q.put((url, params.copy()))  # Use copy to avoid reference issues

        # Check if there are more results
        response = requests.get(url, params=params)
        if response.status_code == 200:
            response_data: Dict[str, Any] = response.json()
            if len(response_data) < params["$limit"]:
                break
        else:
            break  # Stop creating new requests if the request failed

        params["$offset"] += params["$limit"]

    # Create and start the threads
    for _ in range(num_threads):
        t = Thread(target=_worker, args=(q, data_dict))
        t.start()

    # Wait for all tasks to complete
    q.join()

    # Tracker variables
    end_time: float = time.time()
    execution_time: float = end_time - start_time
    dataset_size_megabytes: float = sys.getsizeof(data_dict) / (1024**2)

    # Print the number of records retrieved, execution time, and memory
    print("Total Active Franchise Tax records retrieved:", len(data_dict))
    print(f"Execution time: {execution_time:.1f} seconds")
    print(f"Dataset size: {dataset_size_megabytes} MegaBytes \n")

    # Example: Print the first record
    if data_dict:
        first_record = next(iter(data_dict.values()))
        print(f"First record: {first_record} \n")
    else:
        print("No records retrieved.")

    return data_dict


def _write_dict_to_spark_df(
    spark: SparkSession, 
    data_dict: Dict[str, Any], 
) -> DataFrame:
    """
    Write a dictionary to a Spark DataFrame and save it as a Parquet file.

    Parameters
    ----------
    spark: SparkSession
        The SparkSession object.

    data_dict: Dict[str, Any]
        The input dictionary where each key represents a unique record identifier and each value is a 
        dictionary where the key-value pairs are column names and their values.

    Returns
    -------
    pyspark.sql.DataFrame
        The spark DataFrame containing all scraped Active Franchise Taxholder data.
    """

    # Convert the dictionary to a list of Row objects
    print("Transfering data from JSON structure to PySpark DataFrame...")
    row_data: List[Row] = [Row(**{'taxpayer_number': key, **value}) for key, value in data_dict.items()]
    print("Data successfully written to a PySpark DataFrame. \n")
    return spark.createDataFrame(row_data)

    
def retrieve_franchise_taxholder_df(
    spark: SparkSession,
    save_to_parquet: bool = False,
    output_file: str = "../../data/bronze/texas-comptrollers-office/franchise_tax_payments.parquet",
) -> DataFrame:
    """
    Retrieves franchise tax-holder data from the Texas Comptroller's
    Office database and converts the data into a PySpark DataFrame.

    The method employs multithreading to efficiently send GET requests to the
    Texas Comptroller's Office endpoint. The number of threads used for this operation
    is determined by the number of available CPU cores.

    The collected data can optionally be saved as a Parquet file.

    Parameters
    ----------
    spark: SparkSession
        A SparkSession object to enable the creation of DataFrame.
    save_to_parquet: bool, optional
        If True, the DataFrame is written to a Parquet file. Default is False.
    output_file: str, optional
        The path of the output file where the DataFrame is to be written if
        save_to_parquet is set to True. Default is a specified path.

    Returns
    -------
    pyspark.sql.DataFrame
        A PySpark DataFrame containing active franchise tax permit holder data.

    Raises
    ------
    IOError
        If the GET request fails to successfully pull the desired attributes.
    """

    # Retrieve necessary parameters to call GET request to franchise tax permit holder data
    FRANCHISE_TAX_API_ENDPOINT: str = "https://data.texas.gov/resource/9cir-efmm.json"
    NUM_THREADS: int = multiprocessing.cpu_count()

    # GET request to Texas Comptroller's Office endpoint
    json_data: Dict[str, Any] = _multithreaded_get_request_to_json_endpoint(
        url = FRANCHISE_TAX_API_ENDPOINT,
        num_threads = NUM_THREADS,
    )

    # Write JSON file to a Spark dataframe
    df: DataFrame = _write_dict_to_spark_df(
        spark = spark,
        data_dict = json_data,
    )

    # Write the DataFrame to a Parquet file if prompted to do so
    if save_to_parquet:
        df.write.parquet(output_file)

    return df

