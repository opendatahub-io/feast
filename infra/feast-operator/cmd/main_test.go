/*
Copyright 2024 Feast Community.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/rest"
)

func TestValidateNotebookCRD(t *testing.T) {
	notebookGVK := schema.GroupVersionKind{
		Group:   "kubeflow.org",
		Version: "v1",
		Kind:    "Notebook",
	}

	tests := []struct {
		name        string
		statusCode  int
		wantExists  bool
		wantErr     bool
	}{
		{
			name:       "CRD exists returns true",
			statusCode: http.StatusOK,
			wantExists: true,
			wantErr:    false,
		},
		{
			name:       "CRD not found returns false without error",
			statusCode: http.StatusNotFound,
			wantExists: false,
			wantErr:    false,
		},
		{
			name:       "Forbidden returns false without error",
			statusCode: http.StatusForbidden,
			wantExists: false,
			wantErr:    false,
		},
		{
			name:       "Internal server error returns false with error",
			statusCode: http.StatusInternalServerError,
			wantExists: false,
			wantErr:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if tt.statusCode == http.StatusOK {
					w.WriteHeader(http.StatusOK)
					resp := map[string]interface{}{
						"apiVersion": "apiextensions.k8s.io/v1",
						"kind":       "CustomResourceDefinition",
						"metadata": map[string]interface{}{
							"name": "notebooks.kubeflow.org",
						},
					}
					json.NewEncoder(w).Encode(resp)
				} else {
					w.WriteHeader(tt.statusCode)
					resp := map[string]interface{}{
						"kind":       "Status",
						"apiVersion": "v1",
						"metadata":   map[string]interface{}{},
						"status":     "Failure",
						"message":    "error",
						"reason":     http.StatusText(tt.statusCode),
						"code":       tt.statusCode,
					}
					json.NewEncoder(w).Encode(resp)
				}
			}))
			defer server.Close()

			cfg := &rest.Config{
				Host: server.URL,
			}

			exists, err := validateNotebookCRD(context.Background(), cfg, notebookGVK)

			if tt.wantErr && err == nil {
				t.Errorf("expected error, got nil")
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v", err)
			}
			if exists != tt.wantExists {
				t.Errorf("crdExists = %v, want %v", exists, tt.wantExists)
			}
		})
	}
}
