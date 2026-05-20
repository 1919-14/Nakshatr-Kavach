import pandas as pd; import joblib
from sklearn.preprocessing import MinMaxScaler
df=pd.read_parquet('../download_data/raw/training_xgb.parquet')
feat_cols=[c for c in df.columns if c not in ['timestamp_utc', 'kp_target_3hr', 'kp_target_6hr', 'kp_target_12hr', 'kp_target_24hr']]
X=df[feat_cols].values; scaler=MinMaxScaler().fit(X); joblib.dump(scaler, 'app/models/xgb_scaler.pkl')
df_lstm=pd.read_parquet('../download_data/raw/training_lstm.parquet')
feat_cols=[c for c in df_lstm.columns if c not in ['timestamp_utc', 'kp_target_3hr', 'kp_target_6hr', 'kp_target_12hr', 'kp_target_24hr']]
X_lstm=df_lstm[feat_cols].values; scaler_lstm=MinMaxScaler().fit(X_lstm)
joblib.dump(scaler_lstm, 'app/models/lstm_scaler.pkl')
print('Scalers created successfully.')
